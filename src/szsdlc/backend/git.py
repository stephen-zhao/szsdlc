"""The `git` sdlc backend: the project sdlc store dir is a clone of the
project sdlc store repo, a remote repository and branch that hold the
authoritative copy.

    SZSDLC_BACKEND=git
    SZSDLC_BACKEND_GIT_URL         the remote (required)
    SZSDLC_BACKEND_GIT_BRANCH      the branch to track; default: the remote's
                                   default branch. Created by the first push
                                   when the remote lacks it
    SZSDLC_BACKEND_GIT_SUBDIR      where in the repository the project sdlc root is;
                                   default: its top. `{project}` allowed
    SZSDLC_BACKEND_GIT_CLONE_ROOT  directory clones are made under; default
                                   `~/.claude/szsdlc/git`
    SZSDLC_BACKEND_GIT_CLONE_PATH  the exact clone, overriding CLONE_ROOT.
                                   `{project}` allowed

A sync commits everything in the clone, fetches, rebases onto the remote
branch and pushes — in that order, because git cannot rebase a dirty tree.
That order is also why there is no "record but do not send" operation and
none is needed: a sync that cannot reach the remote has already committed,
says so once, and the next one sends it.

History stays linear, and two machines writing different entities — the
ordinary case, since every entity is its own file or section — rebase cleanly.
When a rebase does conflict it is abandoned rather than left half done: the
local commits stay, nothing is pushed, and the problem names the clone so it
can be resolved by hand.

git is run with prompting disabled and a timeout, because a hook that waits
for a password nobody can type hangs the session it belongs to. Credentials
embedded in the URL are never shown: everything that prints it redacts them.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any, ClassVar

from ..errors import ConfigError
from ..settings import Setting
from . import (  # noqa: F401  (redact is re-exported here, where it lived)
    CONTROL,
    URL_USERINFO,
    Context,
    redact,
    RetiredNames,
    SyncReport,
    path_setting,
    readable_name,
    required,
    subpath,
)

#: Seconds any one git invocation that may reach the network can take.
TIMEOUT = 45

#: Seconds a purely local invocation can take. A local commit makes three of
#: them, and the tightest hook it runs under is UserPromptSubmit, which Claude
#: Code kills after 60 seconds — a budget shared with the fetch and the push.
LOCAL_TIMEOUT = 8

#: The transports git may use. Everything else — `ext::`, and any `foo::` URL
#: that would launch a `git-remote-foo` helper found on PATH — is refused.
PROTOCOLS = ("file", "git", "http", "https", "ssh")

#: Seconds a whole sync may take — SessionStart and UserPromptSubmit allow
#: 60, and `Stop` 120. Each git call gets what is left, so the calls cannot add
#: up past the hook that runs them.
SYNC_BUDGET = 50

#: A `helper::address` URL names a remote helper program to run.
HELPER_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*::")

#: A location git can clone: a URL in a transport git may use, or scp-like
#: `[user@]host:path`, the host a name (two characters or more, so a Windows
#: drive such as `C:` is never one) or a bracketed IPv6 address. A `helper::`
#: URL is not matched here; `from_location` still hands it to `from_settings`,
#: which refuses it.
REMOTE = re.compile(r"^(?:(?:https?|ssh|git|file)://\S+"
                    r"|(?:[\w.+-]+@)?(?:[\w.-]{2,}|\[[0-9A-Fa-f:.]+\]):[^\s:]\S*)$", re.IGNORECASE)


def clone_hint(url: str) -> str:
    """The readable part of a clone's directory name: the repository's name.

    Taken from the URL's path only, never its userinfo or query, so a token in
    `https://tok@host/...` or `...?access_token=...` can never reach a
    directory name. The full URL still goes into the name's hash.
    """
    if "://" in url:
        try:
            parts = urlsplit(url)
            path = parts.path or (parts.hostname or "")
        except ValueError:
            path = ""
    else:  # scp-like: [user@]host:path
        path = url.split(":", 1)[1] if ":" in url else url
    tail = path.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"\.git$", "", tail) or "repository"

#: Where settings built from a location say they came from.
LOCATION_SOURCE = "a location"
PREFIX = "SZSDLC_BACKEND_GIT_"

#: Used only when the user has no git identity configured, so a commit in the
#: hidden clone does not fail on a machine that never needed one before.
FALLBACK_IDENTITY = ("szsdlc", "szsdlc@localhost")


def scrub(text: str) -> str:
    """Free text — git's stderr, say — with credentials in any URL removed."""
    return CONTROL.sub("?", URL_USERINFO.sub(r"\1***@", text))


class GitError(Exception):
    def __init__(self, args: list[str], result: subprocess.CompletedProcess | None,
                 message: str | None = None):
        detail = message or ""
        if result is not None:
            lines = (result.stderr or result.stdout or "").strip().splitlines()
            detail = lines[-1] if lines else f"exit {result.returncode}"
        super().__init__(f"git {args[0]}: {detail}")


class GitBackend(RetiredNames):
    type: ClassVar[str] = "git"
    relocates: ClassVar[bool] = True
    keys: ClassVar[frozenset[str]] = frozenset(
        {"URL", "BRANCH", "SUBDIR", "CLONE_ROOT", "CLONE_PATH"})

    def __init__(self, url: str, clone: Path, *, branch: str | None = None,
                 subdir: str = "", project_asset_root: Path | None = None) -> None:
        self.url = url
        self.branch = branch
        self.subdir = subdir
        self.project_sdlc_store_dir = clone
        self.project_sdlc_root = clone / subdir if subdir else clone
        #: The project asset root the command ran in; the clone must not contain it.
        self.project_asset_root = project_asset_root
        #: When the current sync must be done by (time.monotonic()).
        self._deadline: float | None = None

    @classmethod
    def from_settings(cls, found: dict[str, Setting], context: Context) -> GitBackend:
        url = required(found, cls.type, "URL").value.strip()
        if HELPER_URL.match(url):
            # Refused here as well as by git's protocol policy, which a user's
            # own `protocol.<name>.allow` setting could otherwise reopen.
            raise ConfigError(f"SZSDLC_BACKEND_GIT_URL={redact(url)!r} names a git remote "
                              "helper, which szsdlc does not run.",
                              fix="use a file, git, http(s) or ssh URL")
        if url.startswith("-"):
            # git would read it as an option, and `--upload-pack=...` runs a command.
            raise ConfigError(f"SZSDLC_BACKEND_GIT_URL={redact(url)!r} is not a repository URL.",
                              fix="set SZSDLC_BACKEND_GIT_URL to the remote's URL")
        branch = found["BRANCH"].value.strip() if "BRANCH" in found else None
        if "CLONE_PATH" in found:
            clone = path_setting(found["CLONE_PATH"], context)
        else:
            parent = (path_setting(found["CLONE_ROOT"], context) if "CLONE_ROOT" in found
                      else context.home / "git")
            clone = parent / readable_name(url, branch, hint=clone_hint(url))
        return cls(url, clone, branch=branch or None,
                   subdir=subpath(found.get("SUBDIR"), context),
                   project_asset_root=context.project_asset_root)

    @classmethod
    def from_location(cls, text: str, context: Context) -> GitBackend | None:
        """`URL[#branch]`, for any URL git may use, as this backend; None for anything else.

        A remote-helper or option-like URL is not "not mine": it goes through
        `from_settings` like any other, and is refused there as it always is.
        """
        url, _, branch = str(text).strip().partition("#")
        if not (REMOTE.match(url) or HELPER_URL.match(url) or url.startswith("-")):
            return None
        found = {"URL": Setting(f"{PREFIX}URL", url, LOCATION_SOURCE, context.home)}
        if branch:
            found["BRANCH"] = Setting(f"{PREFIX}BRANCH", branch, LOCATION_SOURCE, context.home)
        return cls.from_settings(found, context)

    @classmethod
    def restore(cls, store: Path, state: dict[str, Any]) -> GitBackend:
        return cls(state["url"], store, branch=state.get("branch"),
                   subdir=state.get("subdir") or "")

    def state(self) -> dict[str, Any]:
        return {"url": self.url, "branch": self.branch, "subdir": self.subdir}

    def describe(self) -> list[tuple[str, str, tuple[str, ...]]]:
        return [("project sdlc store repo", redact(self.url), ("URL",)),
                ("branch", self.branch or "(remote default)", ("BRANCH",)),
                ("subdir", self.subdir or ".", ("SUBDIR",)),
                ("project sdlc store dir", str(self.project_sdlc_store_dir),
                 ("CLONE_PATH", "CLONE_ROOT"))]

    def summary(self) -> str:
        return f"in a clone of the project sdlc store repo {redact(self.url)}, synced automatically"

    # -- plumbing -----------------------------------------------------------

    def _git(self, *args: str, cwd: Path | None = None, check: bool = True,
             identity: bool = False, timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
        cwd = self.project_sdlc_store_dir if cwd is None else cwd
        # Only the listed transports: `ext::` runs a command, and a `foo::` URL
        # runs whatever `git-remote-foo` is on PATH.
        command = ["git", "-c", "protocol.allow=never"]
        for protocol in PROTOCOLS:
            command += ["-c", f"protocol.{protocol}.allow=always"]
        if identity:
            for key, value in zip(("user.name", "user.email"), FALLBACK_IDENTITY):
                if not self._config(key):
                    command += ["-c", f"{key}={value}"]
        command += ["-C", str(cwd), *args]
        # GIT_ALLOW_PROTOCOL overrides every protocol.* setting in git config.
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="",
                   SSH_ASKPASS="", GCM_INTERACTIVE="never",
                   GIT_ALLOW_PROTOCOL=":".join(PROTOCOLS))
        if self._deadline is not None:
            left = self._deadline - time.monotonic()
            if left <= 0:
                raise GitError(list(args), None, "out of time for this hook")
            timeout = max(1, min(timeout, int(left)))
        env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
        try:
            # An argv list with no shell; the URL is vetted in from_settings and
            # passed after `--`, and transports are allowlisted above.
            result = subprocess.run(  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
                command, capture_output=True, text=True, env=env,
                timeout=timeout, stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            raise GitError(list(args), None, "git is not installed") from None
        except subprocess.TimeoutExpired:
            raise GitError(list(args), None, f"timed out after {timeout}s") from None
        except OSError as exc:  # git present but not runnable, say
            raise GitError(list(args), None, str(exc)) from None
        if check and result.returncode != 0:
            raise GitError(list(args), result)
        return result

    def _config(self, key: str) -> str:
        cwd = self.project_sdlc_store_dir if (self.project_sdlc_store_dir / ".git").exists() else None
        command = ["git"] + (["-C", str(cwd)] if cwd else []) + ["config", "--get", key]
        try:
            # An argv list with no shell, reading one fixed key.
            result = subprocess.run(  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
                command, capture_output=True, text=True,
                timeout=LOCAL_TIMEOUT, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return result.stdout.strip()

    def _ok(self, *args: str) -> bool:
        return self._git(*args, check=False).returncode == 0

    def _head_branch(self) -> str:
        return self._git("symbolic-ref", "--short", "HEAD", check=False,
                         timeout=LOCAL_TIMEOUT).stdout.strip()

    def _remote_default(self) -> str:
        """The remote's default branch as the clone recorded it; "" if unknown."""
        ref = self._git("symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False,
                        timeout=LOCAL_TIMEOUT).stdout.strip()
        return ref.split("/", 1)[1] if ref.startswith("origin/") else ""

    def _branch(self) -> str:
        return self.branch or self._head_branch() or "main"

    # -- Backend ------------------------------------------------------------

    def materialize(self) -> None:
        local = self.project_sdlc_store_dir
        self._refuse_overlap()
        if (local / ".git").exists():
            self._verify_clone()
            self._refuse_escape()
            return
        existed = local.exists()
        if existed and any(local.iterdir()):
            raise ConfigError(f"{local} exists and is not a clone of {redact(self.url)}.",
                              fix="point SZSDLC_BACKEND_GIT_CLONE_PATH at an empty or new "
                                  "directory")

        local.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._git("clone", "--quiet", "--origin", "origin", "--", self.url, str(local),
                      cwd=local.parent)
            if self.branch and self._head_branch() != self.branch:
                if self._ok("rev-parse", "--verify", "--quiet",
                            f"refs/remotes/origin/{self.branch}"):
                    self._git("checkout", "--quiet", "-B", self.branch, f"origin/{self.branch}")
                else:
                    # A branch the remote does not have yet: start it empty
                    # here, and the first push creates it.
                    self._git("checkout", "--quiet", "--orphan", self.branch)
                    self._git("rm", "-r", "--quiet", "--cached", "--ignore-unmatch", ".",
                              check=False)
                    self._git("clean", "-fdq", check=False)
        except GitError as exc:
            # A clone killed part way leaves a directory the next attempt would
            # refuse as foreign; it was empty or absent before, so clear it.
            if existed:
                for child in local.iterdir():
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
            else:
                shutil.rmtree(local, ignore_errors=True)
            raise ConfigError(f"could not clone {redact(self.url)} into {local}: "
                              f"{self._redacted(exc)}",
                              fix="check SZSDLC_BACKEND_GIT_URL and your git credentials, "
                                  "then rerun") from None
        # The clone's config holds the URL, credentials and all if it has any:
        # readable by its owner only.
        with contextlib.suppress(OSError):
            (local / ".git" / "config").chmod(0o600)
        self._refuse_escape()

    def _verify_clone(self) -> None:
        """The clone is of our URL, on our branch — or it is not used at all.

        Checked on reuse and again before every sync, because a project sdlc
        store found by path is rebuilt from its recorded entry without settings.
        """
        local = self.project_sdlc_store_dir
        origin = self._config("remote.origin.url")
        pushurl = self._config("remote.origin.pushurl")
        if pushurl and pushurl != self.url:
            raise ConfigError(
                f"{local} pushes to {redact(pushurl)}, not to {redact(self.url)}.",
                fix=f"git -C {local} config --unset remote.origin.pushurl")
        if origin != self.url:
            held = f"a clone of {redact(origin)}" if origin else "a repository with no origin"
            raise ConfigError(
                f"{local} is {held}, not a clone of {redact(self.url)}.",
                fix="point SZSDLC_BACKEND_GIT_CLONE_PATH elsewhere, or remove that clone")
        head = self._head_branch()
        wanted = self.branch or self._remote_default()
        if wanted and head != wanted:
            raise ConfigError(
                f"{local} has {head or 'a detached HEAD'} checked out, not {wanted}.",
                fix=f"git -C {local} checkout {wanted}, or point "
                    "SZSDLC_BACKEND_GIT_CLONE_PATH elsewhere")

    def _refuse_overlap(self) -> None:
        """A clone holding the project asset root would commit and push all of it."""
        if self.project_asset_root is None:
            return
        assets, clone = self.project_asset_root.resolve(), self.project_sdlc_store_dir.resolve()
        if assets == clone or clone in assets.parents:
            raise ConfigError(f"the git backend's clone {self.project_sdlc_store_dir} contains the "
                              f"project asset root {self.project_asset_root}.",
                              fix="point SZSDLC_BACKEND_GIT_CLONE_PATH outside the asset root")

    def _refuse_escape(self) -> None:
        """The project sdlc root and its `.szsdlc/` may be symlinks in the repository;
        both must stay in the clone, or what is written there is never synced."""
        clone = self.project_sdlc_store_dir.resolve()
        for path, what in ((self.project_sdlc_root, f"SZSDLC_BACKEND_GIT_SUBDIR={self.subdir!r}"),
                           (self.project_sdlc_root / ".szsdlc", "the project's .szsdlc directory")):
            resolved = path.resolve()
            if resolved != clone and clone not in resolved.parents:
                raise ConfigError(f"{what} resolves to {resolved}, outside the clone, where "
                                  "nothing is synced.",
                                  fix="make it a real directory in the repository")

    def _redacted(self, exc: Exception) -> str:
        return scrub(str(exc).replace(self.url, redact(self.url)))

    def has_unsent_changes(self) -> bool:
        if not (self.project_sdlc_store_dir / ".git").exists():
            return False
        if self._git("status", "--porcelain", check=False).stdout.strip():
            return True
        return self._ahead(self._branch())

    def _ahead(self, branch: str) -> bool:
        if not self._ok("rev-parse", "--verify", "--quiet", "HEAD"):
            return False
        upstream = f"refs/remotes/origin/{branch}"
        if not self._ok("rev-parse", "--verify", "--quiet", upstream):
            return True
        count = self._git("rev-list", "--count", f"{upstream}..HEAD", check=False)
        return (count.stdout.strip() or "0") != "0"

    def sync(self, reason: str) -> SyncReport:
        report = SyncReport()
        if not (self.project_sdlc_store_dir / ".git").exists():
            report.problem = f"{self.project_sdlc_store_dir} is not a git clone."
            report.fix = "remove it; the next szsdlc command clones it again"
            return report
        self._deadline = time.monotonic() + SYNC_BUDGET
        try:
            if not self.branch and not self._remote_default():
                # Learn the remote's default branch, so the check below can
                # hold a clone to it rather than to whatever it has checked out.
                self._git("remote", "set-head", "origin", "--auto", check=False)
            self._verify_clone()
            branch = self._branch()
            self._commit(reason)
            if self._fetch(report) and self._integrate(branch, report):
                if report.incoming:
                    # What was pulled may have turned SUBDIR into a symlink out.
                    self._refuse_escape()
                self._push(branch, report)
            if report.ok:
                self._check_ignored(report)
        except ConfigError as exc:
            report.problem, report.fix = exc.problem, exc.fix
        except GitError as exc:
            # A rebase that timed out is still in progress; never leave it so.
            # The cleanup is not held to the budget the rebase ran out of.
            self._deadline = None
            self._abort_rebase()
            report.problem = self._redacted(exc)
            report.fix = f"git -C {self.project_sdlc_store_dir} status"
        finally:
            self._deadline = None
        return report

    def refresh(self) -> SyncReport:
        """Fetch, then make `origin/<branch>` the current state exactly.

        Nothing is committed for sending, rebased or pushed. Whatever differed
        from the remote — uncommitted changes and commits never pushed — is
        kept first: committed in the clone and pointed at by
        `refs/szsdlc/replaced/<UTC time>-<commit>`, a ref outside `refs/heads` and
        `refs/tags`, so no push sends it and no branch list shows it. The
        report's `note` names it, so recovering is one `git log` away.
        """
        report = SyncReport()
        if not (self.project_sdlc_store_dir / ".git").exists():
            report.problem = f"{self.project_sdlc_store_dir} is not a git clone."
            report.fix = "remove it; the next szsdlc command clones it again"
            return report
        self._deadline = time.monotonic() + SYNC_BUDGET
        try:
            if not self.branch and not self._remote_default():
                self._git("remote", "set-head", "origin", "--auto", check=False)
            self._verify_clone()
            branch = self._branch()
            try:
                self._git("fetch", "--quiet", "origin")
            except GitError as exc:
                report.problem = (f"could not reach {redact(self.url)} ({self._redacted(exc)}); "
                                  "nothing was replaced.")
                report.fix = f"git -C {self.project_sdlc_store_dir} fetch origin"
                return report
            upstream = f"refs/remotes/origin/{branch}"
            if not self._ok("rev-parse", "--verify", "--quiet", upstream):
                return report  # the remote has no such branch: nothing to refresh to

            dirty = bool(self._git("status", "--porcelain", check=False,
                                   timeout=LOCAL_TIMEOUT).stdout.strip())
            ahead = self._ahead(branch)
            before = self._git("rev-parse", "--verify", "--quiet", "HEAD", check=False,
                               timeout=LOCAL_TIMEOUT).stdout.strip()
            if dirty or ahead:
                if dirty:
                    self._git("add", "--all", timeout=LOCAL_TIMEOUT)
                    self._git("commit", "--quiet", "--no-verify", "-m",
                              "szsdlc: kept by refresh", identity=True, timeout=LOCAL_TIMEOUT)
                kept = self._git("rev-parse", "--short=8", "HEAD",
                                 timeout=LOCAL_TIMEOUT).stdout.strip()
                ref = ("refs/szsdlc/replaced/"
                       + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{kept}")
                self._git("update-ref", ref, "HEAD", timeout=LOCAL_TIMEOUT)
                report.replaced = True
                report.note = f"kept at {ref}"
            self._git("checkout", "--quiet", "-B", branch, upstream, timeout=LOCAL_TIMEOUT)
            self._git("reset", "--quiet", "--hard", upstream, timeout=LOCAL_TIMEOUT)
            after = self._git("rev-parse", "HEAD", timeout=LOCAL_TIMEOUT).stdout.strip()
            report.incoming = dirty or before != after
            if report.incoming:
                self._refuse_escape()
        except ConfigError as exc:
            report.problem, report.fix = exc.problem, exc.fix
        except GitError as exc:
            report.problem = self._redacted(exc)
            report.fix = f"git -C {self.project_sdlc_store_dir} status"
        finally:
            self._deadline = None
        return report

    def _abort_rebase(self) -> None:
        git_dir = self.project_sdlc_store_dir / ".git"
        if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
            try:
                self._git("rebase", "--abort", check=False, timeout=LOCAL_TIMEOUT)
            except GitError:  # pragma: no cover - git itself is failing
                pass

    def _check_ignored(self, report: SyncReport) -> None:
        """Name project files the repository's ignore rules keep out of every sync.

        They are not forced in: the repository's `.gitignore` is its owner's
        statement of what it holds. But a sync that silently leaves project
        files behind is worse than one that says so.
        """
        listed = self._git("ls-files", "--others", "--ignored", "--exclude-standard",
                           "--directory", "--", self.subdir or ".", check=False)
        ignored = [line for line in listed.stdout.splitlines() if line.strip()]
        if ignored:
            count = len(ignored)
            report.problem = (f"{count} path{'s' if count != 1 else ''} in the project "
                              f"{'are' if count != 1 else 'is'} ignored by the repository "
                              f"and not synced: {ignored[0]}"
                              + (f" and {count - 1} more" if count > 1 else "") + ".")
            report.fix = f"git -C {self.project_sdlc_store_dir} check-ignore -v {ignored[0]}"

    def _commit(self, reason: str) -> None:
        self._git("add", "--all", timeout=LOCAL_TIMEOUT)
        if self._git("diff", "--cached", "--quiet", check=False,
                     timeout=LOCAL_TIMEOUT).returncode == 0:
            return
        self._git("commit", "--quiet", "--no-verify", "-m", f"szsdlc: {reason}",
                  identity=True, timeout=LOCAL_TIMEOUT)

    def _fetch(self, report: SyncReport) -> bool:
        try:
            self._git("fetch", "--quiet", "origin")
        except GitError as exc:
            report.problem = (f"could not reach {redact(self.url)} ({self._redacted(exc)}); "
                              "changes are kept locally.")
            report.fix = f"git -C {self.project_sdlc_store_dir} fetch origin"
            return False
        return True

    def _integrate(self, branch: str, report: SyncReport) -> bool:
        upstream = f"refs/remotes/origin/{branch}"
        if not self._ok("rev-parse", "--verify", "--quiet", upstream):
            return True  # nothing remote yet; the push creates it
        if not self._ok("rev-parse", "--verify", "--quiet", "HEAD"):
            # Nothing local yet: an empty clone the remote has since filled.
            self._git("checkout", "--quiet", "-B", branch, f"origin/{branch}")
            report.incoming = True
            return True
        behind = self._git("rev-list", "--count", f"HEAD..{upstream}", check=False)
        if (behind.stdout.strip() or "0") == "0":
            return True
        if self._git("rebase", "--quiet", f"origin/{branch}", check=False,
                     identity=True).returncode != 0:
            self._git("rebase", "--abort", check=False)
            report.problem = (f"local changes conflict with origin/{branch}; "
                              "they are kept locally and were not pushed.")
            report.fix = f"git -C {self.project_sdlc_store_dir} pull --rebase origin {branch}"
            return False
        report.incoming = True
        return True

    def _push(self, branch: str, report: SyncReport) -> None:
        if not self._ahead(branch):
            return
        for attempt in range(2):
            pushed = self._git("push", "--quiet", "--set-upstream", "origin",
                               f"HEAD:refs/heads/{branch}", check=False)
            if pushed.returncode == 0:
                report.outgoing = True
                return
            # Someone pushed in between: take theirs and try once more.
            if attempt == 0 and self._fetch(report) and self._integrate(branch, report):
                continue
            if report.problem is None:
                lines = (pushed.stderr or "").strip().splitlines()
                detail = (lines[-1] if lines else "rejected").replace(self.url,
                                                                      redact(self.url))
                report.problem = (f"could not push to {redact(self.url)} ({detail}); "
                                  "changes are kept locally.")
                report.fix = f"git -C {self.project_sdlc_store_dir} push origin HEAD:{branch}"
            return
