"""SDLC backends: where a project's SDLC records live, and keeping them in step.

The words are a glossary (docs/reference.md, "Backends"), and the code uses
only those words. A project's **project sdlc root** holds its `.szsdlc/` and
its records; a **project asset root** is a directory of code and other assets
the project describes. An **sdlc backend** keeps a **project sdlc store**: it
reads and writes the **project sdlc store dir**, and the project sdlc root is
inside it. Every project has an sdlc backend. By default it is `workdir`,
where the project sdlc store dir is the asset root itself; any other sdlc
backend keeps the records elsewhere, so szsdlc writes nothing into the asset
root:

- `workdir` — the project sdlc store dir is the asset root;
- `dir` — another local directory;
- `git` — a clone of the **project sdlc store repo**, a remote repository and
  branch that hold the authoritative copy.

Settings are read through :mod:`szsdlc.settings` — user-wide in
`~/.claude/settings.json`, overridden per project in
`<project>/.claude/settings[.local].json`, key by key — and are discriminated
by backend:

    SZSDLC_BACKEND                 workdir | dir | git
    SZSDLC_BACKEND_<TYPE>_<KEY>    that backend's own settings

Only the selected backend's keys are read, so a user can keep a git setup
configured while one project switches to `dir`, and a project returns to
ordinary behaviour with `SZSDLC_BACKEND=workdir`. An unknown key in the
selected backend's namespace is refused, because an ignored setting is one
that does not do what it says.

This package has two operations. :meth:`Backend.sync` sends a project sdlc
store's unsent changes and takes in others' changes, as its sdlc backend
defines, and reports what moved which way. :meth:`Backend.refresh` replaces
local state with the sdlc backend's latest and sends nothing; it integrates
nothing either, so it cannot conflict, and it may replace local state that
differed.
How either is done is the implementation's own business, and deliberately not
describable here — `workdir` has nothing to do, `dir` has nothing to do, and
what `git` does is in :mod:`szsdlc.backend.git` and nowhere else. Nothing
outside this package branches on the type, and no caller may assume a commit,
a remote or a network.

**render is not an operation of this package.** Deriving the generated views
from the entities (:mod:`szsdlc.render`) is local, one-way, and knows about
templates and the graph, none of which a backend has any business knowing.
It is nevertheless the first step of every sync, because nothing should
record or send a view that is out of date — so the two are composed one level
up, in :mod:`szsdlc.sync`, which is what the CLI and the hooks call.

Inside a Claude Code turn, nothing is sent as the turn runs. An edit is not a
unit of history — a turn that touches twelve files is one change to the
project — so the turn is the boundary, and the turn-end hook syncs once.
Work is not at risk in between: the edits are on disk in the project sdlc
store dir, and :meth:`Backend.has_unsent_changes` reads it, so the next sync from
whichever entry point reaches it first sweeps in whatever is sitting there.

A sync runs when a session starts, before a prompt is answered, when a
turn ends, and on an explicit `szsdlc sync`. A command typed in a terminal
has no turn end to wait for, so it syncs as soon as it changes something.
A sync riding on another command is best-effort: a command that succeeded is
never reported as failed because an sdlc backend could not finish a sync.
The change stays local, the problem is said once, and the next sync tries
again. An explicit `szsdlc sync` refuses instead, because there the sync is
the whole of what was asked for.

An sdlc backend is a class implementing :class:`Backend`, listed in
:data:`BACKENDS`. The names the glossary retired still work, with a
`DeprecationWarning`, until WI-0068 removes them.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sys
import tempfile
import warnings
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit, urlunsplit
from typing import Any, ClassVar, Protocol

from .. import settings as settings_module
from ..errors import ConfigError
from ..settings import Setting

SELECTOR = "SZSDLC_BACKEND"

#: The project sdlc store dir is the asset root, found by ordinary discovery.
WORKDIR = "workdir"

#: Replaced by the asset root's name in path-like settings, so one remote or
#: directory can hold many projects.
PROJECT_PLACEHOLDER = "{project}"


# ---------------------------------------------------------------------------
# The interface
# ---------------------------------------------------------------------------


@dataclass
class SyncReport:
    """What one sync did. `problem` set means it did not finish.

    Direction, not mechanism: `incoming` is the only field anything branches
    on, and what it asks is "did the project sdlc store dir change under me,
    so must I reload and redraw?" — not "was there a git pull".
    """

    #: Others' changes arrived; reload before reading.
    incoming: bool = False
    #: Unsent changes were sent.
    outgoing: bool = False
    #: Local state that differed from the latest was replaced (refresh only).
    replaced: bool = False
    #: The backend's own detail, such as where replaced state was kept.
    note: str | None = None
    problem: str | None = None
    fix: str | None = None

    @property
    def ok(self) -> bool:
        return self.problem is None


def _deprecated(old: str, new: str) -> None:
    """Warn that `old` is a retired name (WI-0066), pointing at `new`."""
    warnings.warn(f"{old} is deprecated; use {new}", DeprecationWarning, stacklevel=3)


_UNSET: Any = object()


@dataclass(frozen=True, init=False)
class Context:
    """What an sdlc backend may need to resolve its settings."""

    #: The asset root's name, for PROJECT_PLACEHOLDER.
    project: str
    #: szsdlc's own hidden directory, for anything a backend keeps there.
    home: Path
    #: The project asset root the command ran in, which a project sdlc store
    #: dir must stay out of. None when there is not one (a store opened by path).
    project_asset_root: Path | None = None

    def __init__(self, project: str, home: Path, project_asset_root: Path | None = None,
                 *, directory: Path | None = _UNSET) -> None:
        if directory is not _UNSET:
            _deprecated("Context(directory=...)", "Context(project_asset_root=...)")
            project_asset_root = directory
        object.__setattr__(self, "project", project)
        object.__setattr__(self, "home", home)
        object.__setattr__(self, "project_asset_root", project_asset_root)

    @property
    def directory(self) -> Path | None:
        _deprecated("Context.directory", "Context.project_asset_root")
        return self.project_asset_root


class Backend(Protocol):
    """An sdlc backend: one way of keeping a project sdlc store.

    Constructing one must not touch the disk or the network.
    """

    #: The `SZSDLC_BACKEND` value selecting it.
    type: ClassVar[str]
    #: Its key suffixes: `URL` for `SZSDLC_BACKEND_GIT_URL`.
    keys: ClassVar[frozenset[str]]
    #: Whether the project sdlc store dir is somewhere other than the asset
    #: root. True of every sdlc backend but `workdir`, whose store dir is the
    #: asset root and is found by walking up. Such a store is recorded,
    #: because nothing else could find it again. (WI-0067 records every one.)
    relocates: ClassVar[bool]

    @classmethod
    def from_settings(cls, found: dict[str, Setting], context: Context) -> Backend:
        """Build from this backend's own settings, keyed by suffix.

        Raises ConfigError when one is missing or invalid.
        """

    # Optional: `from_location(cls, text, context) -> Backend | None`. A backend
    # that can read a *location* (what a person types to name a project, such
    # as a repository URL) returns itself, built through `from_settings` so
    # every check applies, or None when `text` is not one of its locations; it
    # raises ConfigError for one it recognises and refuses. A backend without
    # it never claims a location. See `from_location` below.

    @classmethod
    def restore(cls, store: Path, state: dict[str, Any]) -> Backend:
        """Rebuild from :meth:`state`, for a project sdlc store dir found on disk."""

    def state(self) -> dict[str, Any]:
        """What :meth:`restore` needs; JSON-serialisable."""

    #: The local directory this sdlc backend reads and writes — the asset
    #: root, another directory, a clone.
    project_sdlc_store_dir: Path
    #: The project sdlc root inside it, where `.szsdlc/config.yml` lives.
    project_sdlc_root: Path

    def describe(self) -> list[tuple[str, str, tuple[str, ...]]]:
        """(label, value, keys) rows for `szsdlc backend`.

        `keys` are the setting suffixes that decided the value, most specific
        first, so the command can say which file each came from.
        """

    def summary(self) -> str:
        """A few words saying where the project sdlc root is."""

    def materialize(self) -> None:
        """Make :attr:`project_sdlc_store_dir` ready, creating it if needed.
        May use the network.

        Raises ConfigError when it cannot, or when the directory holds
        something else.
        """

    def has_unsent_changes(self) -> bool:
        """Whether there are local changes this sdlc backend has not yet sent."""

    def sync(self, reason: str) -> SyncReport:
        """Send unsent changes and take in others', as this sdlc backend defines.

        Best-effort and never raises: a problem goes in the report, with a fix,
        and the caller's own work is never failed because of it. *How* is
        entirely the implementation's business — whether anything is copied,
        committed, or reached over a network is not asked here and must not be
        inferred by anything that calls this.
        """

    def refresh(self) -> SyncReport:
        """Replace local state with this sdlc backend's latest; send nothing.

        The contract, for every backend: afterwards the current state is the
        latest; nothing is sent; nothing is integrated, so nothing can
        conflict. Local state that differed is replaced, and the report says
        so (`replaced`), with whatever detail the backend can give (`note`).
        Whether replaced state is kept anywhere is the backend's decision.
        `incoming` means what it means for :meth:`sync`: reload before reading.

        Never raises; a problem goes in the report. Asking before replacing
        is the caller's business, not this method's.
        """


class RetiredNames:
    """The names the glossary retired, for a backend that uses the new ones.

    Mixed into the built-in backends; each warns and answers with its
    replacement. WI-0068 removes them.
    """

    @property
    def root(self) -> Path:
        _deprecated("Backend.root", "Backend.project_sdlc_root")
        return self.project_sdlc_root  # type: ignore[attr-defined]

    @property
    def store(self) -> Path:
        _deprecated("Backend.store", "Backend.project_sdlc_store_dir")
        return self.project_sdlc_store_dir  # type: ignore[attr-defined]

    def has_local_changes(self) -> bool:
        _deprecated("Backend.has_local_changes()", "Backend.has_unsent_changes()")
        return self.has_unsent_changes()  # type: ignore[attr-defined]


#: Populated on first use, so importing this module costs nothing. Another
#: package may add its own sdlc backends here; the built-ins are added regardless.
BACKENDS: dict[str, type[Backend]] = {}
_builtins_loaded = False


def _load_builtin_backends() -> None:
    global _builtins_loaded
    if _builtins_loaded:
        return
    from .dir import DirBackend
    from .git import GitBackend
    from .workdir import WorkdirBackend

    for cls in (DirBackend, GitBackend, WorkdirBackend):
        BACKENDS.setdefault(cls.type, cls)
    _builtins_loaded = True


def backend_class(type_name: str) -> type[Backend]:
    _load_builtin_backends()
    try:
        return BACKENDS[type_name]
    except KeyError:
        known = ", ".join(sorted(BACKENDS))
        raise ConfigError(f"{SELECTOR}={type_name!r} is not a known backend.",
                          fix=f"set {SELECTOR} to one of: {known}") from None


def key_prefix(type_name: str) -> str:
    return f"{SELECTOR}_{type_name.upper()}_"


# ---------------------------------------------------------------------------
# Helpers for backends
# ---------------------------------------------------------------------------


def required(found: dict[str, Setting], type_name: str, key: str) -> Setting:
    setting = found.get(key)
    if setting is None or not setting.value.strip():
        raise ConfigError(f"{SELECTOR}={type_name} but {key_prefix(type_name)}{key} is not set.",
                          fix=f"set {key_prefix(type_name)}{key}, or {SELECTOR}={WORKDIR}")
    return setting


def path_setting(setting: Setting, context: Context) -> Path:
    """A path-like setting: `~` expanded, `{project}` filled, made absolute."""
    filled = Setting(setting.key, setting.value.strip().replace(PROJECT_PLACEHOLDER,
                                                                context.project),
                     setting.source, setting.base)
    return Path(os.path.abspath(filled.as_path()))


def subpath(setting: Setting | None, context: Context) -> str:
    """A relative path inside a project sdlc store dir, which may not climb out of it."""
    if setting is None:
        return ""
    raw = setting.value.replace(PROJECT_PLACEHOLDER, context.project)
    if PureWindowsPath(raw.strip()).drive:
        raise ConfigError(f"{setting.key}={setting.value!r} names a drive, not a path in the project sdlc store dir.",
                          fix=f"make {setting.key} a relative path inside it")
    parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
    if ".." in parts:
        raise ConfigError(f"{setting.key}={setting.value!r} leaves the project sdlc store dir.",
                          fix=f"make {setting.key} a path inside it, without '..'")
    return "/".join(parts)


def readable_name(*identity: Any, hint: str) -> str:
    """A collision-free directory name that still says what it holds."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", hint).strip("-.") or "store"
    digest = hashlib.sha256(json.dumps(identity, default=str).encode("utf-8")).hexdigest()
    return f"{slug}-{digest[:10]}"


#: The suffix `readable_name` adds: `-` and ten hex digits.
READABLE_SUFFIX = re.compile(r"-[0-9a-f]{10}$")


def project_sdlc_store_dir_name(path: Path | str) -> str:
    """A project sdlc store dir's name for people: without `readable_name`'s hash suffix.

    So no caller has to know how szsdlc names the directories it makes.
    """
    name = Path(path).name
    return READABLE_SUFFIX.sub("", name) or name


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

#: Userinfo in any URL inside free text, such as git's own error messages.
URL_USERINFO = re.compile(r"(\b[A-Za-z][A-Za-z0-9+.-]*://)[^/\s@]+@")

#: Terminal control characters, never printed from a value someone else wrote.
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def redact(url: str) -> str:
    """`url` with any credentials in it replaced, for display.

    `https://token@host/...` and `https://user:secret@host/...` both carry a
    secret; `ssh://git@host/...` and `git@host:path` carry only a user name,
    which is kept because it is often the useful part.
    """
    url = CONTROL.sub("?", url)
    try:
        parts = urlsplit(url)
    except ValueError:
        # Unparseable, but may still carry a secret: scrub it the blunt way.
        return URL_USERINFO.sub(r"\1***@", url).split("?", 1)[0]
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


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def home() -> Path:
    """szsdlc's own hidden directory, beside Claude Code's."""
    return settings_module.claude_config_dir() / "szsdlc"


@dataclass(frozen=True)
class Selection:
    type: str
    #: Where the selector came from; None when it is the default.
    source: str | None
    #: The selected backend's own settings, by suffix.
    found: dict[str, Setting]


def select(start: Path | str | None = None) -> Selection:
    """Which backend is in effect for `start`, with its raw settings."""
    everything = settings_module.load(start)
    chosen = everything.get(SELECTOR)
    type_name = chosen.value.strip().lower() if chosen and chosen.value.strip() else WORKDIR
    prefix = key_prefix(type_name)
    found = {key[len(prefix):]: s for key, s in everything.items() if key.startswith(prefix)}
    return Selection(type_name, chosen.source if chosen else None, found)


def resolve(start: Path | str | None = None) -> Backend:
    """The backend in effect for `start`. Never None: `workdir` is one too."""
    selection = select(start)
    cls = backend_class(selection.type)
    unknown = sorted(set(selection.found) - cls.keys)
    if unknown:
        prefix = key_prefix(selection.type)
        known = ", ".join(f"{prefix}{k}" for k in sorted(cls.keys))
        raise ConfigError(f"{prefix}{unknown[0]} is not a setting of the {selection.type} backend.",
                          fix=f"remove it; {selection.type} reads {known}")
    asset_root = project_asset_root(start)
    return cls.from_settings(selection.found,
                             Context(project=asset_root.name, home=home(),
                                     project_asset_root=asset_root))


def is_folder(text: str) -> bool:
    """Whether `text` names an existing folder on this computer (`~` expanded). Blank never does."""
    if not str(text).strip():
        return False
    try:
        return Path(text).expanduser().is_dir()
    except (RuntimeError, OSError, ValueError):  # `~nosuchuser`, an unreadable path, a NUL
        return False


def from_location(text: str) -> Backend | None:
    """The sdlc backend a *location* names, or None when no backend claims it.

    A location is what a person types to name a project: a folder on this
    computer, or a place an sdlc backend keeps one, such as a repository URL.
    A folder that exists is never claimed here (resolve it as a folder, with
    `project_sdlc_store_for` or `resolve`); anything else is offered to each
    backend in turn, in a stable order, and the first that claims it wins. Its
    settings come from the location alone. Nothing is created: `ensure` is
    the caller's to call. Raises ConfigError for a location a backend
    recognises and refuses, such as a git remote-helper URL.
    """
    text = str(text).strip()
    if not text or is_folder(text):
        return None
    _load_builtin_backends()
    context = Context(project="project", home=home(), project_asset_root=None)
    for type_name in sorted(BACKENDS):
        reader = getattr(BACKENDS[type_name], "from_location", None)
        if reader is None:
            continue
        found = reader(text, context)
        if found is not None:
            return found
    return None


def project_asset_root(start: Path | str | None = None) -> Path:
    """The project asset root `start` belongs to, whose name fills `{project}`.

    The directory holding project settings when there is one, else the nearest
    enclosing git checkout, else `start` itself — so a command run from a
    subdirectory names the same project as one run from the top.
    """
    found = settings_module.project_dir(start)
    if found is not None:
        return found
    here = Path(start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists():
            return candidate
    return here


# ---------------------------------------------------------------------------
# Recorded project sdlc stores
# ---------------------------------------------------------------------------
#
# Recorded so that a project sdlc root found by ordinary discovery — Claude
# editing a file inside a clone, say — is still recognised as kept by its sdlc
# backend, whatever settings happen to be in effect where the command ran.
# The file keeps its old name, `stores.json`, until WI-0067 renames it.


def _project_sdlc_stores_path() -> Path:
    return home() / "stores.json"


def _read_project_sdlc_stores(*, strict: bool = False) -> dict[str, dict]:
    """Every recorded project sdlc store; empty when there are none.

    A file that exists but cannot be read is empty for lookups, but `strict`
    raises instead, so a write never replaces other stores' entries with just
    its own.
    """
    path = _project_sdlc_stores_path()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        if strict:
            raise ConfigError(f"{path} cannot be read ({exc}).",
                              fix=f"repair or remove {path}; szsdlc rebuilds it") from None
        return {}
    if not isinstance(loaded, dict):
        if strict:
            raise ConfigError(f"{path} is not a mapping of project sdlc stores.",
                              fix=f"repair or remove {path}; szsdlc rebuilds it")
        return {}
    return loaded


def _record_project_sdlc_store(backend: Backend) -> None:
    entry = {"type": backend.type, "state": backend.state()}
    key = str(backend.project_sdlc_store_dir)
    if _read_project_sdlc_stores().get(key) == entry:
        return
    path = _project_sdlc_stores_path()
    # Read again under the lock: another process may have recorded one since.
    with _locked(path):
        entries = _read_project_sdlc_stores(strict=True)
        entries[key] = entry
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".stores-",
                                             suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                out.write(json.dumps(entries, indent=2, sort_keys=True))
            os.replace(temporary, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise


def project_sdlc_store_for(path: Path | str | None) -> Backend | None:
    """The recorded project sdlc store whose store dir contains `path`, as its
    sdlc backend, rebuilt from its entry; None when there is none."""
    if path is None:
        return None
    entries = _read_project_sdlc_stores()
    if not entries:
        return None
    here = Path(os.path.abspath(path))
    for candidate in (here, *here.parents):
        entry = entries.get(str(candidate))
        if entry is None:
            continue
        try:
            return backend_class(entry.get("type", "")).restore(candidate,
                                                                entry.get("state") or {})
        except (ConfigError, KeyError, TypeError):
            return None
    return None


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    """One writer at a time per path: a hook and a command can overlap.

    Locked on the project sdlc store dir, so two projects sharing one clone
    never sync at once.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows: run unlocked
        yield
        return
    lock_dir = home() / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    # Resolved, so two paths naming one clone through a symlink share a lock.
    name = hashlib.sha256(str(Path(path).resolve()).encode("utf-8")).hexdigest()[:16]
    with open(lock_dir / f"{name}.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# What the rest of szsdlc calls
# ---------------------------------------------------------------------------


def project_sdlc_root(start: Path | str | None = None) -> Path | None:
    """The project sdlc root the sdlc backend puts in effect for `start`, made ready.

    None when `start` is already inside a recorded project sdlc store —
    ordinary discovery finds the root from there — and None when there is no
    project at all, which the `workdir` backend reports by having no root.
    Otherwise the project sdlc store dir is created if needed, which may reach
    the network.
    """
    if project_sdlc_store_for(start or Path.cwd()) is not None:
        return None
    backend = resolve(start)
    ensure(backend)
    return backend.project_sdlc_root


def relocates(backend: Backend) -> bool:
    """Whether `backend`'s project sdlc store dir is somewhere other than the asset root.

    Defaulted for a backend that does not say, and defaulted to True: a
    backend exists in order to keep records somewhere, and the two errors are
    not equal. A store recorded needlessly is a stale line in the file; a
    store not recorded is a project nothing can find again. (WI-0067 records
    every store and retires this.)
    """
    return getattr(backend, "relocates", True)


def ensure(backend: Backend) -> None:
    with _locked(backend.project_sdlc_store_dir):
        backend.materialize()
    if relocates(backend):
        _record_project_sdlc_store(backend)


def locate(start: Path | str | None = None) -> Backend | None:
    """The sdlc backend serving `start` *right now*, without creating anything.

    None only when a project sdlc store dir outside the asset root has not
    been created yet — an unmaterialized clone, say — so a hook never clones
    to answer the question. `workdir`'s store dir is the asset root, so it
    always locates, which is why `szsdlc sync` works in any szsdlc repository.
    """
    here = start or Path.cwd()
    found = project_sdlc_store_for(here)
    if found is not None:
        return found
    try:
        backend = resolve(here)
    except ConfigError:
        return None
    if not relocates(backend):
        return backend
    return project_sdlc_store_for(backend.project_sdlc_store_dir)


def sync(backend: Backend, reason: str, *, only_if_changed: bool = False) -> SyncReport | None:
    """Sync one project sdlc store. None when there was nothing to do."""
    with _locked(backend.project_sdlc_store_dir):
        if only_if_changed and not backend.has_unsent_changes():
            return None
        return backend.sync(reason)


def refresh(backend: Backend) -> SyncReport:
    """Refresh one project sdlc store, one writer at a time.

    An sdlc backend written before `refresh` existed gets a problem report naming
    its type rather than an AttributeError: a plug-in that cannot refresh is a
    fact to report, not a crash.
    """
    method = getattr(backend, "refresh", None)
    if method is None:
        return SyncReport(problem=f"the {backend.type} backend cannot refresh.",
                          fix="szsdlc sync")
    with _locked(backend.project_sdlc_store_dir):
        return method()


def in_claude_turn() -> bool:
    """Whether this process runs inside a Claude Code session.

    Claude Code sets `CLAUDECODE=1` in the shells it runs commands in. There,
    the Stop hook pushes at the end of the turn, so commands need only commit.
    """
    return os.environ.get("CLAUDECODE") == "1"


def sync_at(start: Path | str | None, reason: str, *,
            only_if_changed: bool = False) -> SyncReport | None:
    """Sync the project sdlc store serving `start`; None when there is none."""
    backend = locate(start)
    if backend is None:
        return None
    return sync(backend, reason, only_if_changed=only_if_changed)


def describe_problem(report: SyncReport | None) -> str | None:
    """A sync problem, in the usual two-line shape.

    Named for the command, because `szsdlc sync` is what a reader can run
    to see it again. It used to say "szsdlc backend sync", which was a
    command and no longer is.
    """
    if report is None or report.ok:
        return None
    text = f"szsdlc sync: {report.problem}"
    if report.fix:
        text += f"\nFix: {report.fix}"
    return text


def warn(report: SyncReport | None) -> None:
    """Say a sync problem on stderr."""
    text = describe_problem(report)
    if text:
        print(text, file=sys.stderr)


# ---------------------------------------------------------------------------
# Retired names (WI-0066): each warns, then does what its replacement does.
# WI-0068 removes them.
# ---------------------------------------------------------------------------


def managed(path: Path | str | None) -> Backend | None:
    _deprecated("szsdlc.backend.managed()", "szsdlc.backend.project_sdlc_store_for()")
    return project_sdlc_store_for(path)


def project_root(start: Path | str | None = None) -> Path | None:
    _deprecated("szsdlc.backend.project_root()", "szsdlc.backend.project_sdlc_root()")
    return project_sdlc_root(start)


def project_directory(start: Path | str | None = None) -> Path:
    _deprecated("szsdlc.backend.project_directory()", "szsdlc.backend.project_asset_root()")
    return project_asset_root(start)


def _registry_path() -> Path:
    _deprecated("szsdlc.backend._registry_path()", "szsdlc.backend._project_sdlc_stores_path()")
    return _project_sdlc_stores_path()
