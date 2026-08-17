"""Every consistency rule, one function each.

`sync` renders an invalid project happily; this is what objects. That is only
coherent because *project* validity and *model* validity are different things:
a finding here is always a statement about the user's work. A broken model
invariant is a szsdlc bug and raises an internal error instead, and the two
must never be conflated in output.

Errors block. Warnings are signals — a tag used once, an inbox going stale —
that a reasonable person may ignore today and act on next week. Nothing is
promoted from one to the other by accident: the level is stated at the rule.

Staleness of generated files is a rule here like any other, implemented with
the render module's `compare()` primitive. `validate` is the umbrella;
rendering is a mechanism it borrows. That is why there is no `sync --check` —
CI and pre-commit ask one question, not two.
"""

from __future__ import annotations

import re

from .config import Config, EntityType
from .context import age_days
from .errors import BadInput
from .graph import Finding, Graph
from .model import Entity, EntityStore
from .roadmap import Roadmap, load_all as load_roadmaps, scheduling_findings
from .text import near_duplicates

_WIKILINK = re.compile(r"\[\[([^\]|#]+)")
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _settled_status(entity_type: EntityType) -> str | None:
    """The last non-terminal status: a definition that is fully valid.

    Read off the workflow rather than named, so the "an approved requirement
    nobody implements" rule works for a project that calls it `ratified`.
    """
    non_terminal = [name for name, state in entity_type.workflow.states.items()
                    if not state.terminal]
    return non_terminal[-1] if non_terminal else None


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def unparseable_files(store: EntityStore) -> list[Finding]:
    return [
        Finding(kind="unparseable", ref=broken.ref,
                message=f"{broken.path.name} could not be read: {broken.error}",
                fix=f"correct {broken.path}")
        for broken in store.unparseable
    ]


def duplicate_ids(config: Config, store: EntityStore) -> list[Finding]:
    findings = []
    for entity_id, path in store.duplicates:
        original = store.get(entity_id)
        where = original.path if original else "(unreadable)"
        findings.append(Finding(
            kind="duplicate-id", ref=entity_id.text,
            message=f"claimed by two paths: {where} and {path}",
            fix=f"szsdlc convert {entity_id.text} {config.type_for_prefix(entity_id.prefix).name}",
        ))
    return findings


def frontmatter_violations(config: Config, store: EntityStore) -> list[Finding]:
    from .model import schema_findings

    findings = []
    for entity in store:
        for message in schema_findings(config, entity):
            findings.append(Finding(
                kind="frontmatter", ref=entity.id.text, message=message,
                fix=f"szsdlc show {entity.id.text}",
            ))
    return findings


def name_mismatches(store: EntityStore) -> list[Finding]:
    """An on-disk name that does not open with the id's canonical form.

    A warning rather than an error: `WI-7-foo` still loads and still resolves,
    it just does not sort next to `WI-0007`.
    """
    findings = []
    for entity in store:
        name = entity.home.name if entity.home else entity.path.stem
        if not name.startswith(entity.id.text):
            findings.append(Finding(
                kind="name-mismatch", ref=entity.id.text, level="warning",
                message=f"stored as {name!r}, which does not open with {entity.id.text}",
                fix=f"rename it to {entity.id.text}-<slug>",
            ))
    return findings


def unknown_statuses(store: EntityStore) -> list[Finding]:
    findings = []
    for entity in store:
        if entity.status is None or entity.status in entity.type.workflow.states:
            continue
        findings.append(Finding(
            kind="unknown-status", ref=entity.id.text,
            message=f"status {entity.status!r} is not in the {entity.type.name} workflow",
            fix=f"szsdlc set {entity.id.text} status={entity.type.workflow.initial}",
        ))
    return findings


# ---------------------------------------------------------------------------
# Relations
# ---------------------------------------------------------------------------


def tombstoned_references(graph: Graph) -> list[Finding]:
    """A warning, not an error: the reference still resolves. The fix is a
    rewrite so the indirection does not accumulate."""
    return [
        Finding(kind="tombstoned-ref", ref=edge.source.text, level="warning",
                message=(f"{edge.kind} points at {edge.target.redirected_from}, which "
                         f"was converted to {edge.target.entity_id.text}"),
                fix=(f"szsdlc unlink {edge.source.text} {edge.kind} "
                     f"{edge.target.redirected_from}"))
        for edge in graph.redirected()
    ]


# ---------------------------------------------------------------------------
# Status against reality
# ---------------------------------------------------------------------------


def gate_disagreements(store: EntityStore) -> list[Finding]:
    """An entity sitting in a status whose own entry conditions it fails.

    Only reachable by hand-editing, which is exactly why it is checked: `set`
    refuses to create the state, and a file people edit is a file that will
    hold it.
    """
    from .workflow import unmet_gates

    findings = []
    for entity in store:
        if entity.status is None:
            continue
        for gate in unmet_gates(entity, entity.status):
            findings.append(Finding(
                kind="gate-disagreement", ref=entity.id.text,
                message=f"is {entity.status} but {gate.reason}",
                fix=gate.remedy,
            ))
    return findings


def progress_disagreements(store: EntityStore) -> list[Finding]:
    findings = []
    for entity in store:
        progress = entity.progress
        if progress is None or not progress.total:
            continue
        if progress.complete and not entity.is_terminal:
            findings.append(Finding(
                kind="progress-disagreement", ref=entity.id.text, level="warning",
                message=f"every task is ticked but status is {entity.status}",
                fix=f"szsdlc set {entity.id.text} "
                    f"{_close_hint(entity)}",
            ))
    return findings


def _close_hint(entity: Entity) -> str:
    from .workflow import suggested_next

    return f"status={suggested_next(entity)}"


# ---------------------------------------------------------------------------
# Definitions against delivery
# ---------------------------------------------------------------------------


def uncovered_definitions(store: EntityStore, graph: Graph) -> list[Finding]:
    findings = []
    for entity in store.with_flag("persistent"):
        settled = _settled_status(entity.type)
        if settled is None or entity.status != settled:
            continue
        for name, declaration in entity.type.derived.items():
            if declaration.kind != "has_incoming" or graph.derive(entity, name):
                continue
            findings.append(Finding(
                kind="uncovered", ref=entity.id.text, level="warning",
                message=f"is {entity.status} but nothing {declaration.relation} it",
                fix=f"szsdlc list --uncovered",
            ))
    return findings


def unsettled_definitions(config: Config, store: EntityStore,
                          graph: Graph) -> list[Finding]:
    """Work delivering against a definition that is not yet — or no longer — valid."""
    findings = []
    for entity in store:
        for relation in config.relations_from(entity.type.name):
            for target in graph.targets(entity, relation.name):
                if not target.resolved:
                    continue
                definition = store.get(target.entity_id)
                if definition is None or not definition.type.persistent:
                    continue
                settled = _settled_status(definition.type)
                if settled is None or definition.status == settled:
                    continue
                findings.append(Finding(
                    kind="unsettled-definition", ref=entity.id.text, level="warning",
                    message=(f"{relation.name} {definition.id.text}, which is "
                             f"{definition.status} rather than {settled}"),
                    fix=f"szsdlc set {definition.id.text} status={settled}",
                ))
    return findings


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------


def intake_findings(config: Config, store: EntityStore, graph: Graph) -> list[Finding]:
    threshold = int((config.data.get("validate") or {}).get("inbox_stale_days", 0))
    findings = []

    for entity in store.with_flag("intake"):
        workflow = entity.type.workflow
        completed = workflow.completed_status

        if entity.status == completed and not graph.incoming(entity):
            findings.append(Finding(
                kind="intake-childless", ref=entity.id.text,
                message=f"is {completed} but nothing was spawned from it",
                fix=f"szsdlc refine {entity.id.text} --into "
                    f"{_first_typed(config)}",
            ))

        if threshold and entity.status == workflow.initial:
            age = age_days(entity)
            if age is not None and age > threshold:
                findings.append(Finding(
                    kind="intake-stale", ref=entity.id.text, level="warning",
                    message=f"has sat in {entity.status} for {age} days",
                    fix=f'szsdlc drop {entity.id.text} --reason "went nowhere"',
                ))
    return findings


def _first_typed(config: Config) -> str:
    return next((t.name for t in config.entity_types.values() if not t.intake),
                "work_item")


# ---------------------------------------------------------------------------
# Tags — warnings only, never errors
# ---------------------------------------------------------------------------


def tag_findings(config: Config, store: EntityStore) -> list[Finding]:
    settings = config.data.get("validate") or {}
    distance = int(settings.get("tag_near_duplicate_distance", 0))
    singleton_threshold = int(settings.get("tag_singleton_threshold", 0))

    counts: dict[str, int] = {}
    unnormalized: list[Finding] = []
    for entity in store:
        for tag in entity.tags:
            counts[tag] = counts.get(tag, 0) + 1
        if entity.raw_tags != entity.tags:
            unnormalized.append(Finding(
                kind="tag-unnormalized", ref=entity.id.text, level="warning",
                message=f"tags are stored unnormalized: {', '.join(entity.raw_tags)}",
                fix=f"szsdlc tag {entity.id.text} {' '.join(entity.tags)}",
            ))

    findings = list(unnormalized)

    for left, right in near_duplicates(list(counts), max_distance=distance):
        findings.append(Finding(
            kind="tag-near-duplicate", ref=left, level="warning",
            message=f"is one edit from {right!r} — likely the same concept, split",
            fix=f"szsdlc list --tag {left}",
        ))

    if singleton_threshold and len(counts) > singleton_threshold:
        for tag, count in sorted(counts.items()):
            if count == 1:
                findings.append(Finding(
                    kind="tag-singleton", ref=tag, level="warning",
                    message=f"is used once across {len(counts)} tags",
                    fix=f"szsdlc list --tag {tag}",
                ))
    return findings


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def orphan_files(config: Config, store: EntityStore) -> list[Finding]:
    findings = []
    for entity in store:
        if entity.home is None:
            continue
        allowed = set(entity.type.artifacts) | {config.entity_filename}
        for path in entity.artifact_files():
            if path.name not in allowed:
                findings.append(Finding(
                    kind="orphan-file", ref=entity.id.text, level="warning",
                    message=f"{path.name} is not a declared artifact of "
                            f"{entity.type.name}",
                    fix=f"add {path.name} to entity_types.{entity.type.name}.artifacts",
                ))
    return findings


def broken_links(config: Config, store: EntityStore, graph: Graph) -> list[Finding]:
    """Wikilinks and relative markdown links that point nowhere."""
    findings = []
    known = {entity.id.text.lower() for entity in store}

    for entity in store:
        sources = [(config.entity_filename, entity.body)]
        for artifact in entity.type.artifacts:
            text = entity.read_artifact(artifact)
            if text is not None:
                sources.append((artifact, text))

        for where, text in sources:
            for match in _WIKILINK.finditer(text):
                target = match.group(1).strip()
                if target.lower() in known:
                    continue
                try:
                    resolved = graph.ids.parse(target).text.lower()
                except BadInput:
                    resolved = None
                if resolved in known:
                    continue
                findings.append(Finding(
                    kind="broken-wikilink", ref=entity.id.text,
                    message=f"{where} links to [[{target}]], which does not exist",
                    fix=f"szsdlc list --limit 20",
                ))

            base = entity.home or entity.path.parent
            for match in _MD_LINK.finditer(text):
                href = match.group(1)
                if "://" in href or href.startswith(("#", "mailto:")):
                    continue
                if (base / href.split("#", 1)[0]).exists():
                    continue
                findings.append(Finding(
                    kind="broken-link", ref=entity.id.text,
                    message=f"{where} links to {href}, which does not exist",
                    fix=f"correct the link in {(base / where)}",
                ))
    return findings


def stale_generated(config: Config, store: EntityStore, graph: Graph,
                    roadmaps: dict[str, Roadmap]) -> list[Finding]:
    """Generated files that would change if regenerated, or were typed into."""
    from . import render as render_module

    findings = []
    renderer = render_module.Renderer(config, store, graph, roadmaps)

    # A record whose dataset is missing, malformed or off-schema cannot be
    # rendered at all. `sync` skips it so one bad file cannot take down every
    # other generated view; saying so is this command's job.
    for problem in renderer.record_problems():
        findings.append(Finding(
            kind="broken-record", ref="record",
            message=problem.problem.rstrip("."),
            fix=problem.fix or "szsdlc validate --verbose",
        ))

    for item in renderer.all():
        if not item.path.is_file():
            findings.append(Finding(
                kind="missing-generated", ref=item.name,
                message=f"{item.path.name} has never been generated",
                fix="szsdlc sync",
            ))
            continue

        text = item.path.read_bytes().decode("utf-8", "replace")
        if render_module.hand_edited(text):
            # Distinguished from staleness on purpose: this one loses work.
            findings.append(Finding(
                kind="hand-edited", ref=item.name,
                message=f"{item.path.name} was edited by hand; the next sync "
                        f"will overwrite it",
                fix="szsdlc sync",
            ))
        elif text != item.content:
            findings.append(Finding(
                kind="stale-generated", ref=item.name,
                message=f"{item.path.name} is out of date",
                fix="szsdlc sync",
            ))
    return _collapse_by_kind(findings)


#: Below this, naming each file is more useful than counting them.
COLLAPSE_THRESHOLD = 3


def _collapse_by_kind(findings: list[Finding]) -> list[Finding]:
    """Fold a repeated generated-file finding into one row.

    Every view being absent is *one* fact about the project — nobody has run
    `sync` yet — and a fresh project has eight of them. Reported one per file
    they fill nearly half a 20-row budget with the same instruction, which
    trains a reader to skim exactly the output that must not be skimmed.
    Hand-edited files stay itemised at any count: which file lost work is the
    whole content of that finding.
    """
    kept: list[Finding] = []
    by_kind: dict[str, list[Finding]] = {}
    for finding in findings:
        if finding.kind in ("missing-generated", "stale-generated"):
            by_kind.setdefault(finding.kind, []).append(finding)
        else:
            kept.append(finding)

    for kind, group in by_kind.items():
        if len(group) < COLLAPSE_THRESHOLD:
            kept.extend(group)
            continue
        state = "have never been generated" if kind == "missing-generated" \
            else "are out of date"
        kept.append(Finding(
            kind=kind, ref="generated",
            message=f"{len(group)} generated files {state} "
                    f"({', '.join(f.ref for f in group[:3])}, …)",
            fix="szsdlc sync",
        ))
    return kept


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


#: Most severe first, and within a level, in the order declared here — so two
#: runs against the same project always report in the same order.
def run(config: Config, store: EntityStore, graph: Graph,
        roadmaps: dict[str, Roadmap] | None = None) -> list[Finding]:
    roadmaps = roadmaps if roadmaps is not None else load_roadmaps(config)

    findings: list[Finding] = []
    findings += unparseable_files(store)
    findings += duplicate_ids(config, store)
    findings += frontmatter_violations(config, store)
    findings += unknown_statuses(store)
    findings += graph.structural_findings()
    findings += tombstoned_references(graph)
    findings += gate_disagreements(store)
    findings += progress_disagreements(store)
    findings += scheduling_findings(config, store, roadmaps)
    findings += uncovered_definitions(store, graph)
    findings += unsettled_definitions(config, store, graph)
    findings += intake_findings(config, store, graph)
    findings += tag_findings(config, store)
    findings += orphan_files(config, store)
    findings += broken_links(config, store, graph)
    findings += name_mismatches(store)
    findings += stale_generated(config, store, graph, roadmaps)

    return sorted(findings, key=lambda f: (0 if f.is_error else 1,))


def summary(findings: list[Finding]) -> tuple[int, int]:
    errors = sum(1 for f in findings if f.is_error)
    return errors, len(findings) - errors
