"""Where a project's files live, and keeping them in step with wherever
that is.

A project is always on a backend. By default it is `workdir`: the project lives
where `.szsdlc/config.yml` is found above the working directory, and there is
nothing to keep in step with. Any other backend moves the project out of the
working directory, so szsdlc writes nothing there:

- `workdir` — the working tree itself;
- `dir` — another local directory;
- `git` — a remote repository, through a local clone it keeps on the side.

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

This package has two operations. :meth:`Backend.sync` brings a store into
step with wherever that backend keeps it, in both directions, and reports what
moved which way. :meth:`Backend.refresh` makes the latest state where the
project is kept the current one and sends nothing; it integrates nothing
either, so it cannot conflict, and it may replace local state that differed.
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
Work is not at risk in between: the edits are on disk in the store, and
:meth:`Backend.has_local_changes` reads it, so the next sync from
whichever entry point reaches it first sweeps in whatever is sitting there.

A sync runs when a session starts, before a prompt is answered, when a
turn ends, and on an explicit `szsdlc sync`. A command typed in a terminal
has no turn end to wait for, so it syncs as soon as it changes something.
A sync riding on another command is best-effort: a command that succeeded is
never reported as failed because a backend could not reach what is behind it.
The change stays local, the problem is said once, and the next sync tries
again. An explicit `szsdlc sync` refuses instead, because there the sync is
the whole of what was asked for.

A backend is a class implementing :class:`Backend`, registered in
:data:`BACKENDS`.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, ClassVar, Protocol

from .. import settings as settings_module
from ..errors import ConfigError
from ..settings import Setting

SELECTOR = "SZSDLC_BACKEND"

#: The project lives in the working tree, found by ordinary discovery.
WORKDIR = "workdir"

#: Replaced by the project directory's name in path-like settings, so one
#: remote or directory can hold many projects.
PROJECT_PLACEHOLDER = "{project}"


# ---------------------------------------------------------------------------
# The interface
# ---------------------------------------------------------------------------


@dataclass
class SyncReport:
    """What one sync did. `problem` set means it did not finish.

    Direction, not mechanism: `incoming` is the only field anything branches
    on, and what it asks is "did the store change under me, so must I reload
    and redraw?" — not "was there a git pull".
    """

    #: State arrived from where the project is kept; reload before reading it.
    incoming: bool = False
    #: Local state was sent there.
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


@dataclass(frozen=True)
class Context:
    """What a backend may need to resolve its settings."""

    #: The project directory's name, for PROJECT_PLACEHOLDER.
    project: str
    #: szsdlc's own hidden directory, for anything a backend keeps on the side.
    home: Path
    #: The working tree the project belongs to, which a store must stay out of.
    directory: Path | None = None


class Backend(Protocol):
    """One way of storing a project, and of keeping it in step with where
    that is.

    Constructing one must not touch the disk or the network.
    """

    #: The `SZSDLC_BACKEND` value selecting it.
    type: ClassVar[str]
    #: Its key suffixes: `URL` for `SZSDLC_BACKEND_GIT_URL`.
    keys: ClassVar[frozenset[str]]
    #: Whether this backend keeps the project somewhere other than the
    #: working tree. True of every backend that exists to move it; False only
    #: of `workdir`, where the store is the tree and is found by walking up.
    #: A relocated store is recorded, because nothing else could find it.
    relocates: ClassVar[bool]

    @classmethod
    def from_settings(cls, found: dict[str, Setting], context: Context) -> Backend:
        """Build from this backend's own settings, keyed by suffix.

        Raises ConfigError when one is missing or invalid.
        """

    @classmethod
    def restore(cls, store: Path, state: dict[str, Any]) -> Backend:
        """Rebuild from :meth:`state`, for a store found on disk."""

    def state(self) -> dict[str, Any]:
        """What :meth:`restore` needs; JSON-serialisable."""

    #: The directory this backend manages — a clone, a directory.
    store: Path
    #: The project root inside it, where `.szsdlc/config.yml` lives.
    root: Path

    def describe(self) -> list[tuple[str, str, tuple[str, ...]]]:
        """(label, value, keys) rows for `szsdlc backend`.

        `keys` are the setting suffixes that decided the value, most specific
        first, so the command can say which file each came from.
        """

    def summary(self) -> str:
        """A few words saying where the project is kept."""

    def materialize(self) -> None:
        """Make :attr:`store` ready, creating it if needed. May use the network.

        Raises ConfigError when it cannot, or when the store holds something else.
        """

    def has_local_changes(self) -> bool:
        """Whether the store holds anything not yet sent where it is kept."""

    def sync(self, reason: str) -> SyncReport:
        """Bring the store and wherever it is kept into step.

        Best-effort and never raises: a problem goes in the report, with a fix,
        and the caller's own work is never failed because of it. *How* is
        entirely the implementation's business — whether anything is copied,
        committed, or reached over a network is not asked here and must not be
        inferred by anything that calls this.
        """

    def refresh(self) -> SyncReport:
        """Make the latest state where the project is kept the current one.

        The contract, for every backend: afterwards the current state is the
        latest; nothing is sent; nothing is integrated, so nothing can
        conflict. Local state that differed is replaced, and the report says
        so (`replaced`), with whatever detail the backend can give (`note`).
        Whether replaced state is kept anywhere is the backend's decision.
        `incoming` means what it means for :meth:`sync`: reload before reading.

        Never raises; a problem goes in the report. Asking before replacing
        is the caller's business, not this method's.
        """


#: Populated on first use, so importing this module costs nothing. Another
#: package may add its own backends here; the built-ins are added regardless.
BACKENDS: dict[str, type[Backend]] = {}
_builtins_loaded = False


def _load_registry() -> None:
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
    _load_registry()
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
    """A relative path inside a store, which may not climb out of it."""
    if setting is None:
        return ""
    raw = setting.value.replace(PROJECT_PLACEHOLDER, context.project)
    if PureWindowsPath(raw.strip()).drive:
        raise ConfigError(f"{setting.key}={setting.value!r} names a drive, not a path in the store.",
                          fix=f"make {setting.key} a relative path inside it")
    parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
    if ".." in parts:
        raise ConfigError(f"{setting.key}={setting.value!r} leaves the store.",
                          fix=f"make {setting.key} a path inside it, without '..'")
    return "/".join(parts)


def readable_name(*identity: Any, hint: str) -> str:
    """A collision-free directory name that still says what it holds."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", hint).strip("-.") or "store"
    digest = hashlib.sha256(json.dumps(identity, default=str).encode("utf-8")).hexdigest()
    return f"{slug}-{digest[:10]}"


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
    directory = project_directory(start)
    return cls.from_settings(selection.found,
                             Context(project=directory.name, home=home(), directory=directory))


def project_directory(start: Path | str | None = None) -> Path:
    """The working tree `start` belongs to, whose name fills `{project}`.

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
# Managed stores
# ---------------------------------------------------------------------------
#
# Recorded so that a project root found by ordinary discovery — Claude editing
# a file inside a clone, say — is still recognised as backend-managed, whatever
# settings happen to be in effect where the command ran.


def _registry_path() -> Path:
    return home() / "stores.json"


def _read_registry(*, strict: bool = False) -> dict[str, dict]:
    """The registry; empty when there is none.

    A registry that exists but cannot be read is empty for lookups, but
    `strict` raises instead, so a write never replaces other stores' entries
    with just its own.
    """
    path = _registry_path()
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
            raise ConfigError(f"{path} is not a mapping of stores.",
                              fix=f"repair or remove {path}; szsdlc rebuilds it")
        return {}
    return loaded


def _register(backend: Backend) -> None:
    entry = {"type": backend.type, "state": backend.state()}
    key = str(backend.store)
    if _read_registry().get(key) == entry:
        return
    path = _registry_path()
    # Read again under the lock: another process may have registered since.
    with _locked(path):
        entries = _read_registry(strict=True)
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


def managed(path: Path | str | None) -> Backend | None:
    """The backend managing the store that contains `path`, if any."""
    if path is None:
        return None
    entries = _read_registry()
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
def _locked(store: Path) -> Iterator[None]:
    """One writer at a time per path: a hook and a command can overlap."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows: run unlocked
        yield
        return
    lock_dir = home() / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    # Resolved, so two paths naming one clone through a symlink share a lock.
    name = hashlib.sha256(str(Path(store).resolve()).encode("utf-8")).hexdigest()[:16]
    with open(lock_dir / f"{name}.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# What the rest of szsdlc calls
# ---------------------------------------------------------------------------


def project_root(start: Path | str | None = None) -> Path | None:
    """The project root a backend puts in effect for `start`, made ready.

    None when `start` is already inside a managed store — ordinary discovery
    finds the root from there — and None when there is no project at all, which
    the `workdir` backend reports by having no root. Otherwise the store is
    created if needed, which may reach the network.
    """
    if managed(start or Path.cwd()) is not None:
        return None
    backend = resolve(start)
    ensure(backend)
    return backend.root


def relocates(backend: Backend) -> bool:
    """Whether `backend` keeps the project away from the working tree.

    Defaulted for a backend that does not say, and defaulted to True: a
    backend exists in order to move a project somewhere, and the two errors
    are not equal. A store recorded needlessly is a stale line in a registry;
    a store not recorded is a project nothing can find again.
    """
    return getattr(backend, "relocates", True)


def ensure(backend: Backend) -> None:
    with _locked(backend.store):
        backend.materialize()
    if relocates(backend):
        _register(backend)


def locate(start: Path | str | None = None) -> Backend | None:
    """The backend serving `start` *right now*, without creating anything.

    None only when a relocated store has not been created yet — an
    unmaterialized clone, say — so a hook never clones to answer the question.
    A backend that has not moved the project anywhere is always there, which is
    why `workdir` always locates and `szsdlc sync` works in any szsdlc
    repository.
    """
    here = start or Path.cwd()
    found = managed(here)
    if found is not None:
        return found
    try:
        backend = resolve(here)
    except ConfigError:
        return None
    if not relocates(backend):
        return backend
    return managed(backend.store)


def sync(backend: Backend, reason: str, *, only_if_changed: bool = False) -> SyncReport | None:
    """Sync one store. None when there was nothing to do."""
    with _locked(backend.store):
        if only_if_changed and not backend.has_local_changes():
            return None
        return backend.sync(reason)


def refresh(backend: Backend) -> SyncReport:
    """Refresh one store, one writer at a time.

    A backend registered before `refresh` existed gets a problem report naming
    its type rather than an AttributeError: a plug-in that cannot refresh is a
    fact to report, not a crash.
    """
    method = getattr(backend, "refresh", None)
    if method is None:
        return SyncReport(problem=f"the {backend.type} backend cannot refresh.",
                          fix="szsdlc sync")
    with _locked(backend.store):
        return method()


def in_claude_turn() -> bool:
    """Whether this process runs inside a Claude Code session.

    Claude Code sets `CLAUDECODE=1` in the shells it runs commands in. There,
    the Stop hook pushes at the end of the turn, so commands need only commit.
    """
    return os.environ.get("CLAUDECODE") == "1"


def sync_at(start: Path | str | None, reason: str, *,
            only_if_changed: bool = False) -> SyncReport | None:
    """Sync whatever managed store serves `start`; None when there is none."""
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
