"""The operations on a project as a whole and where it is kept.

`render` redraws the views; `sync` sends and takes; `refresh` takes the latest
without sending; `backend` says where the project is kept; `init` makes one.
All but `render` act on *where a project is kept* before a project is loaded,
so they take the path to start from rather than a loaded project.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from pathlib import Path

from .. import config as config_module
from ..errors import BadInput, Refused, SzsdlcError
from . import _queries
from ._project import Project
from ._registry import operation
from ._types import Data


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:  # pragma: no cover - paths are always under the root
        return str(path)


# ------------------------------------------------------------------- render

@dataclass(frozen=True)
class RenderParams(Data):
    pass


@dataclass(frozen=True)
class RenderResult(Data):
    #: The generated files this render rewrote.
    changed: tuple[str, ...]


@operation("render", params=RenderParams, result=RenderResult, kind="mutation")
def render(params: RenderParams, project: Project) -> RenderResult:
    """Regenerate every view and record. Never validates, never reaches a remote.

    Strict, unlike the redraw a mutation ends with: asked for by name, a
    template that will not draw is the answer.
    """
    from .. import render as render_module

    changed = render_module.render(project.config, project.store, _queries.graph(project),
                                   _queries.roadmaps(project))
    return RenderResult(tuple(_relative(project.config.root, item.path) for item in changed))


# --------------------------------------------------------------------- sync

@dataclass(frozen=True)
class SyncParams(Data):
    pass


@dataclass(frozen=True)
class SyncResult(Data):
    changed: tuple[str, ...]
    #: State arrived, and the views were redrawn from it.
    incoming: bool = False
    #: Local state was sent.
    outgoing: bool = False


def send(where: str | None, reason: str = "sync"):
    """The legs of a sync that reach the backend, after the render.

    What arrives is drawn by a second pass, because views cannot be rendered
    from entities that have not arrived yet. Returns the backend's report, or
    None when there was nothing to do. The CLI's `sync` calls this after its
    own render, so it can report the draw before anything is sent.
    """
    from .. import sync as sync_module

    report = sync_module.run(Project(where), reason, should_render=False)
    if report is not None and report.incoming:
        redraw = sync_module.run(Project(where), "redraw after sync", only_if_changed=True)
        if redraw is not None:
            # What arrived and what was sent are the first pass's; a problem
            # is whichever pass had one.
            report = dataclasses.replace(redraw, incoming=True,
                                         outgoing=report.outgoing or redraw.outgoing)
    return report


@operation("sync", params=SyncParams, result=SyncResult, kind="mutation", loads=False)
def sync(params: SyncParams, where: str | None) -> SyncResult:
    """Render, then bring the project into step with where it is kept.

    Explicitly asked for, so a failure refuses, where the sync after another
    command only warns. `sync` stays explicit: a turn is the caller's to
    define, and REQ-0014's one record per turn holds by the caller syncing
    once per turn.
    """
    from .. import backend as backend_module

    backend_module.project_root(where)  # create the store if needed
    rendered = render(RenderParams(), Project(where))
    report = send(where)
    if report is not None and not report.ok:
        raise Refused(report.problem, fix=report.fix)
    return SyncResult(rendered.changed, bool(report and report.incoming),
                      bool(report and report.outgoing))


# ------------------------------------------------------------------ refresh

@dataclass(frozen=True)
class RefreshParams(Data):
    #: Replace local state that differs from the latest. Without it, that
    #: case refuses: it is the one that can lose something.
    force: bool = False


@dataclass(frozen=True)
class RefreshResult(Data):
    incoming: bool
    replaced: bool
    #: The backend's own detail, such as where replaced state was kept.
    note: str | None = None


@operation("refresh", params=RefreshParams, result=RefreshResult, kind="mutation", loads=False)
def refresh(params: RefreshParams, where: str | None) -> RefreshResult:
    """Make the latest state where the project is kept the current one.

    Sends nothing and integrates nothing. Renders afterwards, as `sync`
    does, so views match what arrived — the only local write.
    """
    from .. import backend as backend_module
    from .. import sync as sync_module

    backend_module.project_root(where)  # make the store ready, as any command does
    backend = backend_module.locate(where)
    if backend is None:  # pragma: no cover - project_root just made it
        raise BadInput("refresh: no project here.", fix="szsdlc init")
    if not params.force and backend.has_local_changes():
        raise Refused(
            "refresh: local changes differ from where the project is kept, and "
            "refreshing would replace them.",
            fix="szsdlc refresh --force",
            see="szsdlc sync sends them instead",
        )

    report = backend_module.refresh(backend)
    if not report.ok:
        raise Refused(f"refresh: {report.problem}", fix=report.fix or "szsdlc backend")
    try:
        sync_module.render(Project(where))
    except SzsdlcError:
        pass  # a project that will not load after refreshing is validate's to report
    return RefreshResult(bool(report.incoming), bool(report.replaced), report.note)


# ------------------------------------------------------------------ backend

@dataclass(frozen=True)
class BackendParams(Data):
    pass


@dataclass(frozen=True)
class BackendResult(Data):
    type: str
    #: Where the choice came from: a settings source, "default", or that the
    #: path is inside the backend's own store.
    source: str
    inside: bool
    #: (label, value) for each setting in effect, and the root.
    rows: tuple[tuple[str, str], ...]


def _printable(text: str) -> str:
    # Every value here came from a settings file someone else may have
    # written; none of it may reach a terminal as a control sequence.
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "?", text)


@operation("backend", params=BackendParams, result=BackendResult, kind="query", loads=False)
def backend(params: BackendParams, where: str | None) -> BackendResult:
    """Where the project is kept, and why. Inspection only."""
    from .. import backend as backend_module

    selection = backend_module.select(where)
    # Asked first, not as a fallback: standing inside a store is a fact about
    # where you are.
    inside = backend_module.managed(where or Path.cwd())
    if inside is not None:
        current = inside
        source = "this directory is inside its store"
        rows = [(label, value) for label, value, _ in current.describe()]
    else:
        current = backend_module.resolve(where)
        source = selection.source or "default"
        found = selection.found
        rows = [(label, f"{value}  ({next((found[k].source for k in keys if k in found), 'default')})")
                for label, value, keys in current.describe()]
    if current.root is not None:
        rows.append(("root", str(current.root)
                     + ("" if current.store.exists() else "  (not created yet)")))
    return BackendResult(_printable(current.type), _printable(source), inside is not None,
                         tuple((_printable(label), _printable(value)) for label, value in rows))


# --------------------------------------------------------------------- init

STARTER_CONFIG = """\
# szsdlc project configuration.
#
# This file is deep-merged over the built-in defaults, so everything below is
# optional and an empty file is already a valid project. Adjust one flag:
#
#   entity_types: {{spike: {{persistent: true}}}}
#
# drop a type you do not want (`entity_types: {{spike: null}}`), or replace a
# whole section outright (`entity_types: {{_replace: true, ...}}`).
project:
  name: {name}
"""


@dataclass(frozen=True)
class InitParams(Data):
    name: str | None = None


@dataclass(frozen=True)
class InitResult(Data):
    root: str
    #: What was made, relative to the root: the config, then each directory.
    created: tuple[str, ...]


@operation("init", params=InitParams, result=InitResult, kind="mutation",
           interfaces=frozenset({"cli"}), loads=False)
def init(params: InitParams, where: str | None) -> InitResult:
    from .. import backend as backend_module
    from ..roadmap import Roadmap

    # With a backend in effect the project goes wherever it keeps it, never
    # into the directory the command was run from.
    root = backend_module.project_root(where) or Path(where or Path.cwd()).resolve()
    config_path = config_module.config_path_for(root)
    if config_path.exists():
        raise BadInput(
            f"{config_path} already exists.",
            fix="szsdlc context",
        )

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_bytes(STARTER_CONFIG.format(name=params.name or root.name).encode("utf-8"))

    config = config_module.load(root)
    created = [config_path]
    for entity_type in config.entity_types.values():
        directory = config.dir_for(entity_type)
        directory.mkdir(parents=True, exist_ok=True)
        created.append(directory)
    for directory in (config.roadmaps_dir, config.views_dir, config.records_dir,
                      config.standards_dir, config.templates_dir):
        directory.mkdir(parents=True, exist_ok=True)
        created.append(directory)
    for name in config.roadmaps:
        roadmap = Roadmap.load(config, name)
        roadmap.save()
        created.append(roadmap.path)
    return InitResult(str(root), tuple(_relative(root, path) for path in created))
