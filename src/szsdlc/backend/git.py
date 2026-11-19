"""The `git` backend: the project lives in a remote repository.

    SZSDLC_BACKEND=git
    SZSDLC_BACKEND_GIT_URL         the remote (required)
    SZSDLC_BACKEND_GIT_BRANCH      the branch to track; default: the remote's
                                   default branch. Created by the first push
                                   when the remote lacks it
    SZSDLC_BACKEND_GIT_SUBDIR      where in the repository the project lives;
                                   default: its top. `{project}` allowed
    SZSDLC_BACKEND_GIT_CLONE_ROOT  directory clones are made under; default
                                   `~/.claude/szsdlc/git`
    SZSDLC_BACKEND_GIT_CLONE_PATH  the exact clone, overriding CLONE_ROOT.
                                   `{project}` allowed

A commit records everything in the clone locally. A sync commits, rebases
onto the remote branch and pushes. History stays linear, and two machines writing different entities —
the ordinary case, since every entity is its own file or section — rebase
cleanly. When a rebase does conflict it is abandoned rather than left half
done: the local commits stay, nothing is pushed, and the problem names the
clone so it can be resolved by hand.

git is run with prompting disabled and a timeout, because a hook that waits
for a password nobody can type hangs the session it belongs to. Credentials
embedded in the URL are never shown: everything that prints it redacts them.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit, urlunsplit

from ..errors import ConfigError
from ..settings import Setting
from . import (
    Context,
    SyncReport,
    path_setting,
    readable_name,
    required,
    subpath,
)

#: Seconds any one git invocation that may reach the network can take.
TIMEOUT = 45

#: Seconds a purely local invocation can take. A local commit makes three of
#: them, inside an edit hook that Claude Code kills after 30 seconds.
LOCAL_TIMEOUT = 8

#: The transports git may use. Everything else — `ext::`, and any `foo::` URL
#: that would launch a `git-remote-foo` helper found on PATH — is refused.
PROTOCOLS = ("file", "git", "http", "https", "ssh")

#: Used only when the user has no git identity configured, so a commit in the
#: hidden clone does not fail on a machine that never needed one before.
FALLBACK_IDENTITY = ("szsdlc", "szsdlc@localhost")


def redact(url: str) -> str:
    """`url` with any credentials in it replaced, for display.

    `https://token@host/...` and `https://user:secret@host/...` both carry a
    secret; `ssh://git@host/...` and `git@host:path` carry only a user name,
    which is kept because it is often the useful part.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.query:
        # `?access_token=...` and the like: a query has no business in a git
        # remote other than to carry a secret.
        parts = parts._replace(query="***")
    if "@" in parts.netloc:
        userinfo, host = parts.netloc.rsplit("@", 1)
        if parts.scheme in ("http", "https") or ":" in userinfo:
            user = "" if parts.scheme in ("http", "https") else userinfo.split(":", 1)[0] + ":"
            parts = parts._replace(netloc=f"{user}***@{host}")
    return urlunsplit(parts) if parts != urlsplit(url) else url


class GitError(Exception):
    def __init__(self, args: list[str], result: subprocess.CompletedProcess | None,
                 message: str | None = None):
        detail = message or ""
        if result is not None:
            lines = (result.stderr or result.stdout or "").strip().splitlines()
            detail = lines[-1] if lines else f"exit {result.returncode}"
        super().__init__(f"git {args[0]}: {detail}")


class GitBackend:
    type: ClassVar[str] = "git"
    keys: ClassVar[frozenset[str]] = frozenset(
        {"URL", "BRANCH", "SUBDIR", "CLONE_ROOT", "CLONE_PATH"})

    def __init__(self, url: str, clone: Path, *, branch: str | None = None,
                 subdir: str = "", working_tree: Path | None = None) -> None:
        self.url = url
        self.branch = branch
        self.subdir = subdir
        self.store = clone
        self.root = clone / subdir if subdir else clone
        #: The tree the project belongs to; the clone must not contain it.
        self.working_tree = working_tree

    @classmethod
    def from_settings(cls, found: dict[str, Setting], context: Context) -> GitBackend:
        url = required(found, cls.type, "URL").value.strip()
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
            tail = re.sub(r"\.git$", "", url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1])
            clone = parent / readable_name(url, branch, hint=tail)
        return cls(url, clone, branch=branch or None,
                   subdir=subpath(found.get("SUBDIR"), context),
                   working_tree=context.directory)

    @classmethod
    def restore(cls, store: Path, state: dict[str, Any]) -> GitBackend:
        return cls(state["url"], store, branch=state.get("branch"),
                   subdir=state.get("subdir") or "")

    def state(self) -> dict[str, Any]:
        return {"url": self.url, "branch": self.branch, "subdir": self.subdir}

    def describe(self) -> list[tuple[str, str, tuple[str, ...]]]:
        return [("url", redact(self.url), ("URL",)),
                ("branch", self.branch or "(remote default)", ("BRANCH",)),
                ("subdir", self.subdir or ".", ("SUBDIR",)),
                ("clone", str(self.store), ("CLONE_PATH", "CLONE_ROOT"))]

    def summary(self) -> str:
        return f"local clone of {redact(self.url)}, synced automatically"

    # -- plumbing -----------------------------------------------------------

    def _git(self, *args: str, cwd: Path | None = None, check: bool = True,
             identity: bool = False, timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
        cwd = self.store if cwd is None else cwd
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
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="",
                   SSH_ASKPASS="", GCM_INTERACTIVE="never")
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
        cwd = self.store if (self.store / ".git").exists() else None
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
        local = self.store
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
        self._refuse_escape()

    def _verify_clone(self) -> None:
        """The clone is of our URL, on our branch — or it is not used at all.

        Checked on reuse and again before every commit and sync, because a
        store found by path is restored from the registry without settings.
        """
        local = self.store
        origin = self._config("remote.origin.url")
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
        """A clone holding the working tree would commit and push all of it."""
        if self.working_tree is None:
            return
        tree, clone = self.working_tree.resolve(), self.store.resolve()
        if tree == clone or clone in tree.parents:
            raise ConfigError(f"the git backend's clone {self.store} contains the working "
                              f"tree {self.working_tree}.",
                              fix="point SZSDLC_BACKEND_GIT_CLONE_PATH outside the working tree")

    def _refuse_escape(self) -> None:
        """SUBDIR may be a symlink in the repository; it must stay in the clone."""
        if not self.subdir:
            return
        clone, root = self.store.resolve(), self.root.resolve()
        if root != clone and clone not in root.parents:
            raise ConfigError(f"SZSDLC_BACKEND_GIT_SUBDIR={self.subdir!r} resolves to {root}, "
                              f"outside the clone, where nothing is synced.",
                              fix="make SZSDLC_BACKEND_GIT_SUBDIR a real directory in the "
                                  "repository")

    def _redacted(self, exc: Exception) -> str:
        return str(exc).replace(self.url, redact(self.url))

    def has_local_changes(self) -> bool:
        if not (self.store / ".git").exists():
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

    def commit(self, reason: str) -> SyncReport:
        report = SyncReport()
        if not (self.store / ".git").exists():
            report.problem = f"{self.store} is not a git clone."
            report.fix = "remove it; the next szsdlc command clones it again"
            return report
        try:
            self._verify_clone()
            self._commit(reason)
        except ConfigError as exc:
            report.problem, report.fix = exc.problem, exc.fix
        except GitError as exc:
            report.problem = self._redacted(exc)
            report.fix = f"git -C {self.store} status"
        return report

    def sync(self, reason: str) -> SyncReport:
        report = SyncReport()
        if not (self.store / ".git").exists():
            report.problem = f"{self.store} is not a git clone."
            report.fix = "remove it; the next szsdlc command clones it again"
            return report
        try:
            self._verify_clone()
            branch = self._branch()
            self._commit(reason)
            if self._fetch(report) and self._integrate(branch, report):
                if report.pulled:
                    # What was pulled may have turned SUBDIR into a symlink out.
                    self._refuse_escape()
                self._push(branch, report)
            if report.ok:
                self._check_ignored(report)
        except ConfigError as exc:
            report.problem, report.fix = exc.problem, exc.fix
        except GitError as exc:
            # A rebase that timed out is still in progress; never leave it so.
            self._abort_rebase()
            report.problem = self._redacted(exc)
            report.fix = f"git -C {self.store} status"
        return report

    def _abort_rebase(self) -> None:
        git_dir = self.store / ".git"
        if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
            try:
                self._git("rebase", "--abort", check=False)
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
            report.fix = f"git -C {self.store} check-ignore -v {ignored[0]}"

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
            report.fix = f"git -C {self.store} fetch origin"
            return False
        return True

    def _integrate(self, branch: str, report: SyncReport) -> bool:
        upstream = f"refs/remotes/origin/{branch}"
        if not self._ok("rev-parse", "--verify", "--quiet", upstream):
            return True  # nothing remote yet; the push creates it
        if not self._ok("rev-parse", "--verify", "--quiet", "HEAD"):
            # Nothing local yet: an empty clone the remote has since filled.
            self._git("checkout", "--quiet", "-B", branch, f"origin/{branch}")
            report.pulled = True
            return True
        behind = self._git("rev-list", "--count", f"HEAD..{upstream}", check=False)
        if (behind.stdout.strip() or "0") == "0":
            return True
        if self._git("rebase", "--quiet", f"origin/{branch}", check=False,
                     identity=True).returncode != 0:
            self._git("rebase", "--abort", check=False)
            report.problem = (f"local changes conflict with origin/{branch}; "
                              "they are kept locally and were not pushed.")
            report.fix = f"git -C {self.store} pull --rebase origin {branch}"
            return False
        report.pulled = True
        return True

    def _push(self, branch: str, report: SyncReport) -> None:
        if not self._ahead(branch):
            return
        for attempt in range(2):
            pushed = self._git("push", "--quiet", "--set-upstream", "origin",
                               f"HEAD:refs/heads/{branch}", check=False)
            if pushed.returncode == 0:
                report.pushed = True
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
                report.fix = f"git -C {self.store} push origin HEAD:{branch}"
            return
