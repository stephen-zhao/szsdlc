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
    #: root: False only for `workdir`. Read by `relocates()` for readers
    #: outside szsdlc; nothing in szsdlc decides by it (WI-0067 records every
    #: store, whatever its type).
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
# Recorded project sdlc stores (WI-0067)
# ---------------------------------------------------------------------------
#
# Every real project szsdlc has run on, whatever its sdlc backend type, is
# recorded in `project_sdlc_stores.json` in szsdlc's home: keyed by project
# sdlc root, with its project sdlc store dir, its backend type and state, and
# the asset roots that have used it. Each asset root is also linked to the one
# project sdlc root it uses, and the two maps are always written together.
#
# The record lets a path no settings reach (Claude editing a file inside a
# clone) still be recognised as kept by its sdlc backend, and lets readers list
# projects. Where settings are in effect they decide, and the record follows:
# see `_record_decides`.

#: The file before WI-0067, keyed by project sdlc store dir. Read once, to
#: migrate from, and never written again.
LEGACY_STORES_FILE = "stores.json"


def _project_sdlc_stores_path() -> Path:
    return home() / "project_sdlc_stores.json"


def _empty_record() -> dict[str, dict]:
    return {"project_sdlc_stores": {}, "asset_roots": {}}


def _refuse_record(path: Path, problem: str) -> ConfigError:
    return ConfigError(f"{path} {problem}.", fix=f"repair or remove {path}; szsdlc rebuilds it")


def _read_record(*, strict: bool = False) -> dict[str, dict]:
    """The record: `{"project_sdlc_stores": {...}, "asset_roots": {...}}`.

    Empty when there is none. A file that exists but cannot be read, or holds
    an entry of the wrong shape, is empty (or loses that entry) for lookups,
    but `strict` raises instead, so a write never replaces other stores'
    entries with just its own. With no file yet but a `stores.json` from before
    WI-0067, that is migrated, in memory only: the migration is saved by the
    next write, which holds the lock, so a read (`validate` included) never
    writes.
    """
    path = _project_sdlc_stores_path()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _migrated_record(strict=strict)
    except (OSError, ValueError, RecursionError) as exc:  # nested past the decoder's depth
        if strict:
            raise _refuse_record(path, f"cannot be read ({exc})") from None
        return _empty_record()
    if (not isinstance(loaded, dict) or not isinstance(loaded.get("project_sdlc_stores"), dict)
            or not isinstance(loaded.get("asset_roots"), dict)):
        if strict:
            raise _refuse_record(path, "is not a record of project sdlc stores")
        return _empty_record()
    record = _well_formed(loaded)
    if strict and record != {"project_sdlc_stores": loaded["project_sdlc_stores"],
                             "asset_roots": loaded["asset_roots"]}:
        raise _refuse_record(path, "holds an entry that is not a project sdlc store or an asset root link")
    return record


def _well_formed(loaded: dict) -> dict[str, dict]:
    """Only the entries of the right shape: every path a string, every list a list of them."""
    def is_path(value: Any) -> bool:  # a NUL byte is no path: `Path.is_dir()` raises on it
        return isinstance(value, str) and "\0" not in value

    def is_store(entry: Any) -> bool:
        return (isinstance(entry, dict) and is_path(entry.get("dir"))
                and isinstance(entry.get("backend_type"), str)
                and isinstance(entry.get("backend_state"), dict)
                and isinstance(entry.get("asset_roots"), list)
                and all(is_path(a) for a in entry["asset_roots"]))
    return {"project_sdlc_stores": {root: entry for root, entry in loaded["project_sdlc_stores"].items()
                                    if is_path(root) and is_store(entry)},
            "asset_roots": {asset: root for asset, root in loaded["asset_roots"].items()
                            if is_path(asset) and is_path(root)}}


def _migrated_record(*, strict: bool) -> dict[str, dict]:
    """`stores.json` (keyed by project sdlc store dir) as the new record, in memory; empty without one.

    Only real projects carry over: a store whose project sdlc root holds
    `.szsdlc/config.yml`. A `stores.json` that exists but cannot be read
    refuses a `strict` (writing) read, so its projects are never dropped by a
    record written without them.
    """
    from .. import config as config_module

    legacy = home() / LEGACY_STORES_FILE
    try:
        old = json.loads(legacy.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty_record()
    except (OSError, ValueError, RecursionError) as exc:  # nested past the decoder's depth
        if strict:
            raise _refuse_record(legacy, f"cannot be read to migrate it ({exc})") from None
        return _empty_record()
    record = _empty_record()
    for store_dir, entry in (old.items() if isinstance(old, dict) else ()):
        if not isinstance(entry, dict):
            continue
        try:
            backend = backend_class(entry.get("type", "")).restore(Path(store_dir), entry.get("state") or {})
        except (ConfigError, KeyError, TypeError):
            continue
        root = backend.project_sdlc_root
        if root is None or not config_module.config_path_for(Path(root)).is_file():
            continue
        record["project_sdlc_stores"][str(root)] = _entry_for(backend, [])
    return record


def _entry_for(backend: Backend, asset_roots: list[str]) -> dict[str, Any]:
    return {"asset_roots": sorted(set(asset_roots)), "dir": str(backend.project_sdlc_store_dir),
            "backend_type": backend.type, "backend_state": backend.state()}


def _write_record(record: dict[str, dict]) -> None:
    """Replace the record atomically; the caller holds the lock."""
    path = _project_sdlc_stores_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".project_sdlc_stores-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(json.dumps(record, indent=2, sort_keys=True))
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def _with_store(record: dict[str, dict], backend: Backend, asset_root: Path | None) -> dict[str, dict]:
    """`record` with `backend`'s store, and `asset_root` linked to it, as they are now."""
    root = str(backend.project_sdlc_root)
    stores = {key: dict(value) for key, value in record["project_sdlc_stores"].items()}
    links = dict(record["asset_roots"])
    roots = list((stores.get(root) or {}).get("asset_roots") or [])
    if asset_root is not None:
        asset = str(asset_root)
        previous = links.get(asset)
        if previous and previous != root and previous in stores:  # it moved: leave the old list
            stores[previous]["asset_roots"] = [a for a in stores[previous].get("asset_roots", []) if a != asset]
        links[asset] = root
        roots.append(asset)
    stores[root] = _entry_for(backend, roots)
    return {"project_sdlc_stores": stores, "asset_roots": links}


def _record_project_sdlc_store(backend: Backend, asset_root: Path | None = None) -> None:
    """Record `backend`'s store, and link `asset_root` to it, when that changes anything.

    Only a real project is recorded: one whose project sdlc root holds
    `.szsdlc/config.yml`. A command run in a scratch folder writes nothing.
    """
    from .. import config as config_module

    root = backend.project_sdlc_root
    if root is None or not config_module.config_path_for(Path(root)).is_file():
        return
    path = _project_sdlc_stores_path()
    # Compared under the lock, so a prune running at the same time is never
    # undone by a stale "nothing changed": a write only on change.
    with _locked(path):
        record = _read_record(strict=True)
        updated = _with_store(record, backend, asset_root)
        if updated != record or not path.is_file():  # an in-memory migration is saved here
            _write_record(updated)


def _restore(root: str, entry: Any) -> Backend | None:
    if not isinstance(entry, dict):
        return None
    try:
        return backend_class(entry.get("backend_type", "")).restore(Path(entry.get("dir") or root),
                                                                    entry.get("backend_state") or {})
    except (ConfigError, KeyError, TypeError):
        return None


def _recorded_entry(path: Path | str | None) -> tuple[str, dict] | None:
    """The recorded `(project sdlc root, entry)` serving `path`, as `project_sdlc_store_for` finds it."""
    if path is None:
        return None
    record = _read_record()
    stores, links = record["project_sdlc_stores"], record["asset_roots"]
    if not stores:
        return None
    here = Path(os.path.abspath(path))
    lineage = (here, *here.parents)
    for candidate in lineage:
        root = links.get(str(candidate))
        if root is not None and isinstance(stores.get(root), dict):
            return root, stores[root]
    for candidate in lineage:
        if isinstance(stores.get(str(candidate)), dict):
            return str(candidate), stores[str(candidate)]
    for candidate in lineage:
        holding = [root for root, entry in stores.items()
                   if isinstance(entry, dict) and entry.get("dir") == str(candidate)]
        if len(holding) == 1:
            return holding[0], stores[holding[0]]
        if holding:
            return None  # shared by several projects: none of them is this path's
    return None


def project_sdlc_store_for(path: Path | str | None) -> Backend | None:
    """The recorded project sdlc store serving `path`, as its sdlc backend; None when there is none.

    In order: the store an asset root containing `path` is linked to (the
    nearest asset root wins); else the nearest recorded project sdlc root
    above `path`, so a path in one subdirectory project never resolves to its
    sibling; else the one store whose project sdlc store dir contains `path`
    (a path at the top of a clone, above its project). This is the record's
    answer alone: where settings select a backend, they decide (see
    `project_sdlc_root`).
    """
    found = _recorded_entry(path)
    return _restore(*found) if found else None


def _record_decides(path: Path | str | None) -> Backend | None:
    """The recorded store `path` is inside, when that store is kept away from its asset roots.

    There no settings can answer: they would treat the project sdlc store dir
    itself (a clone, a `dir` store elsewhere) as an asset root. Everywhere else
    the settings decide, so a project switched to another backend type in its
    settings is served by that type, and its next `ensure` rewrites the record.
    A store nested inside one of its asset roots is reached by that root's
    settings, and a `workdir` store *is* its asset root, so neither is kept
    away. A store with no asset roots recorded yet (migrated, or recorded by a
    caller outside szsdlc) is taken to be kept away: nothing says otherwise.
    """
    here = Path(os.path.abspath(path or Path.cwd()))
    found = _recorded_entry(here)
    if found is None:
        return None
    root, entry = found
    store_dir = Path(entry.get("dir") or root)
    if not (here == store_dir or store_dir in here.parents):
        return None
    assets = [Path(a) for a in entry.get("asset_roots") or []]
    if any(store_dir == a or a in store_dir.parents for a in assets):
        return None
    return _restore(root, entry)


def stale_project_sdlc_stores(record: dict[str, dict] | None = None) -> list[tuple[str, str, str]]:
    """What in the record no longer holds, as `(kind, key, problem)`.

    A store whose project sdlc root or dir is gone, an asset root that is gone,
    and a list and a link that disagree. Reported, never removed here: a drive
    that is not mounted must not lose its entry. `szsdlc backend prune`
    removes them.
    """
    record = _read_record() if record is None else record
    stores, links = record["project_sdlc_stores"], record["asset_roots"]
    found: list[tuple[str, str, str]] = []
    for root, entry in sorted(stores.items()):
        entry = entry if isinstance(entry, dict) else {}
        if not Path(root).is_dir() or not Path(entry.get("dir") or root).is_dir():
            found.append(("store", root, f"project sdlc store {root} no longer exists"))
            continue
        for asset in entry.get("asset_roots") or []:
            if not Path(asset).is_dir():
                found.append(("asset_root", asset, f"asset root {asset} of {root} no longer exists"))
            elif links.get(asset) != root:
                found.append(("listed", asset, f"asset root {asset} is listed under {root} but linked elsewhere"))
    for asset, root in sorted(links.items()):
        if root not in stores:
            found.append(("link", asset, f"asset root {asset} is linked to {root}, which is not recorded"))
        elif asset not in (stores[root].get("asset_roots") or []):
            found.append(("link", asset, f"asset root {asset} is linked to {root}, which does not list it"))
    return found


def prune_project_sdlc_stores() -> list[str]:
    """Remove what `stale_project_sdlc_stores` reports; one line per removal."""
    path = _project_sdlc_stores_path()
    with _locked(path):
        record = _read_record(strict=True)
        stale = stale_project_sdlc_stores(record)
        if not stale:
            return []
        stores = {key: dict(value) for key, value in record["project_sdlc_stores"].items()}
        links = dict(record["asset_roots"])
        removed: list[str] = []
        for kind, key, problem in stale:
            if kind == "store":
                stores.pop(key, None)
                for asset in [a for a, r in links.items() if r == key]:
                    links.pop(asset)
            elif kind == "asset_root":  # gone: from every list, and its link
                for entry in stores.values():
                    entry["asset_roots"] = [a for a in entry.get("asset_roots", []) if a != key]
                links.pop(key, None)
            elif kind == "listed":  # only from the lists of stores it is not linked to
                for store_root, entry in stores.items():
                    if links.get(key) != store_root:
                        entry["asset_roots"] = [a for a in entry.get("asset_roots", []) if a != key]
            else:  # a link to nothing, or to a store that does not list it
                links.pop(key, None)
            removed.append(f"removed: {problem}")
        _write_record({"project_sdlc_stores": stores, "asset_roots": links})
        return removed


@dataclass(frozen=True)
class ProjectSdlcStore:
    """One recorded project sdlc store, as `project_sdlc_stores()` lists it (WI-0063)."""

    project_sdlc_root: Path
    project_sdlc_store_dir: Path
    #: Every asset root that has used it, sorted.
    asset_roots: tuple[Path, ...]
    backend_type: str
    #: Its project sdlc root and dir are both still directories: the same test
    #: `stale_project_sdlc_stores` reports a gone store by.
    exists: bool


def project_sdlc_stores() -> list[ProjectSdlcStore]:
    """Every recorded project sdlc store, sorted by project sdlc root. Public; reads only.

    Read through szsdlc's own reader: a record that cannot be read, or an
    entry of the wrong shape, lists nothing for it, as lookups see it, and a
    `stores.json` from before WI-0067 is listed through its in-memory
    migration. Never writes. Backend state (a URL may carry credentials) is
    not listed; `szsdlc backend` shows it, redacted, for one project.
    """
    stores = _read_record()["project_sdlc_stores"]
    listed = []
    for root, entry in sorted(stores.items()):
        store_dir = Path(entry["dir"] or root)
        listed.append(ProjectSdlcStore(
            project_sdlc_root=Path(root), project_sdlc_store_dir=store_dir,
            asset_roots=tuple(Path(a) for a in sorted(set(entry["asset_roots"]))),
            backend_type=entry["backend_type"],
            exists=Path(root).is_dir() and store_dir.is_dir()))
    return listed


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

    None when `start` is inside a recorded project sdlc store dir kept away
    from its asset roots (see `_record_decides`) — ordinary discovery finds
    the root from there — and None when there is no
    project at all, which the `workdir` backend reports by having no root.
    Otherwise the project sdlc store dir is created if needed, which may reach
    the network.
    """
    if _record_decides(start or Path.cwd()) is not None:
        return None  # inside a store kept elsewhere: discovery finds the root from here
    backend = resolve(start)
    ensure(backend, asset_root=project_asset_root(start))
    return backend.project_sdlc_root


def relocates(backend: Backend) -> bool:
    """Whether `backend`'s project sdlc store dir is somewhere other than the asset root.

    Nothing in szsdlc decides by this any more: since WI-0067 every store is
    recorded, whatever its type. It stays for readers outside szsdlc, and is
    defaulted to True for a backend that does not say.
    """
    return getattr(backend, "relocates", True)


def ensure(backend: Backend, asset_root: Path | str | None = None) -> None:
    """Make `backend`'s project sdlc store dir ready, then record it (a real project only).

    `asset_root`, when given, is linked to the store, and leaves the list of
    any project it used before. Recording is best effort: a record that cannot
    be read is left as it is, and `szsdlc backend` reports it.
    """
    with _locked(backend.project_sdlc_store_dir):
        backend.materialize()
    # Best effort: the record is per user, and a damaged one must never block
    # a project's work. It is left as it is (never overwritten), and
    # `szsdlc backend` says it cannot be read.
    with contextlib.suppress(ConfigError, OSError):
        _record_project_sdlc_store(backend, Path(asset_root) if asset_root is not None else None)


def locate(start: Path | str | None = None) -> Backend | None:
    """The sdlc backend serving `start` *right now*, without creating anything.

    None only when a project sdlc store dir outside the asset root has not
    been created yet — an unmaterialized clone, say — so a hook never clones
    to answer the question. `workdir`'s store dir is the asset root, so it
    always locates, which is why `szsdlc sync` works in any szsdlc repository.
    """
    here = start or Path.cwd()
    found = _record_decides(here)
    if found is not None:
        return found
    try:
        backend = resolve(here)
    except ConfigError:
        return None
    return backend if Path(backend.project_sdlc_store_dir).is_dir() else None


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


def _read_project_sdlc_stores(*, strict: bool = False) -> dict[str, dict]:
    """The recorded stores alone, keyed by project sdlc root (before WI-0067: by store dir)."""
    return _read_record(strict=strict)["project_sdlc_stores"]


def _registry_path() -> Path:
    _deprecated("szsdlc.backend._registry_path()", "szsdlc.backend._project_sdlc_stores_path()")
    return _project_sdlc_stores_path()
