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

A sync commits everything in the clone, rebases onto the remote branch and
pushes. History stays linear, and two machines writing different entities —
the ordinary case, since every entity is its own file or section — rebase
cleanly. When a rebase does conflict it is abandoned rather than left half
done: the local commits stay, nothing is pushed, and the problem names the
clone so it can be resolved by hand.

git is run with prompting disabled and a timeout, because a hook that waits
for a password nobody can type hangs the session it belongs to.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, ClassVar

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

#: Seconds any one git invocation may take.
TIMEOUT = 45

#: Used only when the user has no git identity configured, so a commit in the
#: hidden clone does not fail on a machine that never needed one before.
FALLBACK_IDENTITY = ("szsdlc", "szsdlc@localhost")


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
                 subdir: str = "") -> None:
        self.url = url
        self.branch = branch
        self.subdir = subdir
        self.store = clone
        self.root = clone / subdir if subdir else clone

    @classmethod
    def from_settings(cls, found: dict[str, Setting], context: Context) -> GitBackend:
        url = required(found, cls.type, "URL").value.strip()
        branch = found["BRANCH"].value.strip() if "BRANCH" in found else None
        if "CLONE_PATH" in found:
            clone = path_setting(found["CLONE_PATH"], context)
        else:
            parent = (path_setting(found["CLONE_ROOT"], context) if "CLONE_ROOT" in found
                      else context.home / "git")
            tail = re.sub(r"\.git$", "", url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1])
            clone = parent / readable_name(url, branch, hint=tail)
        return cls(url, clone, branch=branch or None,
                   subdir=subpath(found.get("SUBDIR"), context))

    @classmethod
    def restore(cls, store: Path, state: dict[str, Any]) -> GitBackend:
        return cls(state["url"], store, branch=state.get("branch"))

    def state(self) -> dict[str, Any]:
        return {"url": self.url, "branch": self.branch}

    def describe(self) -> list[tuple[str, str, tuple[str, ...]]]:
        return [("url", self.url, ("URL",)),
                ("branch", self.branch or "(remote default)", ("BRANCH",)),
                ("subdir", self.subdir or ".", ("SUBDIR",)),
                ("clone", str(self.store), ("CLONE_PATH", "CLONE_ROOT"))]

    def summary(self) -> str:
        return f"local clone of {self.url}, synced automatically"

    # -- plumbing -----------------------------------------------------------

    def _git(self, *args: str, cwd: Path | None = None, check: bool = True,
             identity: bool = False) -> subprocess.CompletedProcess:
        cwd = self.store if cwd is None else cwd
        command = ["git"]
        if identity:
            for key, value in zip(("user.name", "user.email"), FALLBACK_IDENTITY):
                if not self._config(key):
                    command += ["-c", f"{key}={value}"]
        command += ["-C", str(cwd), *args]
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="",
                   SSH_ASKPASS="", GCM_INTERACTIVE="never")
        env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
        try:
            result = subprocess.run(command, capture_output=True, text=True, env=env,
                                    timeout=TIMEOUT, stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            raise GitError(list(args), None, "git is not installed") from None
        except subprocess.TimeoutExpired:
            raise GitError(list(args), None, f"timed out after {TIMEOUT}s") from None
        if check and result.returncode != 0:
            raise GitError(list(args), result)
        return result

    def _config(self, key: str) -> str:
        cwd = self.store if (self.store / ".git").exists() else None
        command = ["git"] + (["-C", str(cwd)] if cwd else []) + ["config", "--get", key]
        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=TIMEOUT, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return result.stdout.strip()

    def _ok(self, *args: str) -> bool:
        return self._git(*args, check=False).returncode == 0

    def _head_branch(self) -> str:
        return self._git("symbolic-ref", "--short", "HEAD", check=False).stdout.strip()

    def _branch(self) -> str:
        return self.branch or self._head_branch() or "main"

    # -- Backend ------------------------------------------------------------

    def materialize(self) -> None:
        local = self.store
        if (local / ".git").exists():
            origin = self._config("remote.origin.url")
            if origin and origin != self.url:
                raise ConfigError(
                    f"{local} is a clone of {origin}, not of {self.url}.",
                    fix="point SZSDLC_BACKEND_GIT_CLONE_PATH elsewhere, or remove that clone")
            return
        if local.exists() and any(local.iterdir()):
            raise ConfigError(f"{local} exists and is not a clone of {self.url}.",
                              fix="point SZSDLC_BACKEND_GIT_CLONE_PATH at an empty or new "
                                  "directory")

        local.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._git("clone", "--quiet", "--origin", "origin", self.url, str(local),
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
            raise ConfigError(f"could not clone {self.url} into {local}: {exc}",
                              fix="check SZSDLC_BACKEND_GIT_URL and your git credentials, "
                                  "then rerun") from None

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

    def sync(self, reason: str) -> SyncReport:
        report = SyncReport()
        if not (self.store / ".git").exists():
            report.problem = f"{self.store} is not a git clone."
            report.fix = "remove it; the next szsdlc command clones it again"
            return report
        branch = self._branch()
        try:
            self._commit(reason)
            if self._fetch(report) and self._integrate(branch, report):
                self._push(branch, report)
        except GitError as exc:
            report.problem = str(exc)
            report.fix = f"git -C {self.store} status"
        return report

    def _commit(self, reason: str) -> None:
        self._git("add", "--all")
        if self._git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return
        self._git("commit", "--quiet", "--no-verify", "-m", f"szsdlc: {reason}",
                  identity=True)

    def _fetch(self, report: SyncReport) -> bool:
        try:
            self._git("fetch", "--quiet", "origin")
        except GitError as exc:
            report.problem = f"could not reach {self.url} ({exc}); changes are kept locally."
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
                report.problem = (f"could not push to {self.url} "
                                  f"({lines[-1] if lines else 'rejected'}); "
                                  "changes are kept locally.")
                report.fix = f"git -C {self.store} push origin HEAD:{branch}"
            return
