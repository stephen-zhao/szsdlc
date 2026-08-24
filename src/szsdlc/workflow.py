"""Status transitions and the gates guarding them.

A gate is checked on *entering* a status, so the requirement lives in one place
— the state being entered — rather than being restated on every transition into
it. Two gates ship: an artifact must exist and be non-empty, and every checkbox
in the progress artifact must be ticked.

Every refusal here has to end in something the caller can run, because an agent
that gets "blocked" without a next move spends its next three calls guessing.
Where a legal alternative transition exists, that is the fix; where the fix is
writing a file, the refusal names the path.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import State
from .errors import Refused
from .model import Entity


@dataclass(frozen=True)
class Transition:
    entity: str
    before: str | None
    after: str

    def __str__(self) -> str:
        return f"{self.entity}: status {self.before} → {self.after}"


@dataclass(frozen=True)
class Gate:
    """One unmet entry condition, with the way to meet it.

    `prefer_remedy` decides what a refusal offers as its `Fix:`. Set it when
    the remedy is the *only* real answer — no legal transition creates a
    missing file — and leave it off when moving somewhere else is a genuine
    alternative. Unfinished tasks are the latter: "go back to executing" is a
    true statement about where the work belongs, whereas "results.md is
    missing, so return to running" is the same bad advice as suggesting the
    caller abandon the work, wearing a legal transition as a disguise.
    """

    reason: str
    remedy: str
    prefer_remedy: bool = False

    def __str__(self) -> str:
        return self.reason


def unmet_gates(entity: Entity, status: str) -> list[Gate]:
    """Which entry conditions of `status` this entity does not yet meet."""
    state: State | None = entity.type.workflow.states.get(status)
    if state is None:
        return []

    gates: list[Gate] = []

    for artifact in state.requires_artifact:
        if entity.has_artifact(artifact):
            continue
        path = entity.artifact_path(artifact)
        if path is not None:
            remedy = f"write {path}, then rerun"
        elif entity.type.is_dynamic_layout:
            # It has nowhere to put one *yet*. Needing an artifact is how an
            # entry earns a directory, so the fix is to give it one, not a shrug.
            remedy = f"szsdlc layout {entity.id.text} directory, then write {artifact}"
        else:
            remedy = f"write {artifact} (this type has no directory), then rerun"
        gates.append(Gate(
            reason=f"{artifact} is missing or empty",
            remedy=remedy,
            prefer_remedy=True,
        ))

    if state.requires_tasks_complete:
        progress = entity.progress
        if progress is None:
            gates.append(Gate(
                # A misconfiguration, not a state of the work: no transition
                # anywhere fixes it, so the remedy is the only answer.
                reason=f"{entity.type.name} declares no progress artifact to check",
                remedy=f"set entity_types.{entity.type.name}.progress_artifact",
                prefer_remedy=True,
            ))
        elif not progress.complete:
            unchecked = progress.remaining if progress.total else "no"
            gates.append(Gate(
                reason=f"{progress} tasks complete, {unchecked} unchecked",
                remedy=f"tick the remaining boxes in "
                       f"{entity.type.progress_artifact}, then rerun",
            ))

    return gates


def _alternative(entity: Entity, blocked: str) -> str | None:
    """A legal transition from here that is not blocked — the best fix line.

    Offering the move the caller *can* make turns a refusal into a next action
    rather than a dead end. Abandoning is excluded: "your design is missing, so
    drop the work item" is worse advice than no advice at all, and a suggestion
    an agent might actually follow.
    """
    workflow = entity.type.workflow
    current = workflow.states.get(entity.status or "")
    if current is None:
        return None
    for candidate in current.to:
        state = workflow.states.get(candidate)
        if candidate == blocked or state is None or state.abandoned:
            continue
        if not unmet_gates(entity, candidate):
            return candidate
    return None


def suggested_next(entity: Entity) -> str:
    """A status this entity could legally move to right now.

    Every refusal that has to name *some* status uses this rather than the
    workflow's initial one. Suggesting `initial` looks reasonable and is
    usually wrong: an entity sitting at `idea` would be told to move to `idea`,
    and the fix would fail with "already idea" — turning a two-call recovery
    into a loop.
    """
    workflow = entity.type.workflow
    current = workflow.states.get(entity.status or "")
    if current:
        for candidate in current.to:
            state = workflow.states.get(candidate)
            if state is not None and not state.abandoned:
                return candidate
        if current.to:
            return current.to[0]
    return workflow.initial


def step_toward(entity: Entity, target: str) -> str | None:
    """The first hop on the shortest legal path from here to `target`.

    `idea → ready` is two transitions, so telling a caller to "set status=ready"
    produces a refusal rather than progress. Naming the next hop is the
    difference between a fix and a suggestion.
    """
    workflow = entity.type.workflow
    start = entity.status or ""
    if start not in workflow.states or target not in workflow.states:
        return None
    if start == target:
        return None

    frontier = [(start, None)]
    seen = {start}
    while frontier:
        current, first = frontier.pop(0)
        for candidate in workflow.states[current].to:
            if candidate in seen:
                continue
            hop = first or candidate
            if candidate == target:
                return hop
            seen.add(candidate)
            frontier.append((candidate, hop))
    return None


def check(entity: Entity, status: str) -> None:
    """Refuse the move to `status`, or return silently."""
    workflow = entity.type.workflow
    ref = entity.id.text

    if status not in workflow.states:
        raise Refused(
            f"{ref}: {status!r} is not a status of {entity.type.name}.",
            fix=f"szsdlc set {ref} status={suggested_next(entity)}",
            see=f"statuses: {', '.join(workflow.states)}",
        )

    if entity.status == status:
        raise Refused(
            f"{ref}: already {status}.",
            fix=f"szsdlc show {ref}",
        )

    if entity.status not in workflow.states:
        # A hand-edited status loads, but `set` will not move from one: there is
        # no transition graph to consult.
        raise Refused(
            f"{ref}: current status {entity.status!r} is not in the "
            f"{entity.type.name} workflow, so no transition from it is defined.",
            fix=f"szsdlc set {ref} status={workflow.initial}",
            see=f"statuses: {', '.join(workflow.states)}",
        )

    if not workflow.can_move(entity.status or "", status):
        legal = workflow.states[entity.status or ""].to
        raise Refused(
            f"{ref}: {entity.status} → {status} is not a legal transition.",
            fix=(f"szsdlc set {ref} status={legal[0]}" if legal
                 else f"szsdlc show {ref}"),
            see=(f"from {entity.status}: {', '.join(legal)}" if legal
                 else f"{entity.status} is terminal"),
        )

    gates = unmet_gates(entity, status)
    if gates:
        alternative = None if gates[0].prefer_remedy else _alternative(entity, status)
        raise Refused(
            f"{ref}: status {entity.status} → {status} blocked, {gates[0].reason}.",
            fix=(f"szsdlc set {ref} status={alternative}" if alternative
                 else gates[0].remedy),
            see=None if len(gates) == 1 else f"also: {gates[1].reason}",
        )


def move(entity: Entity, status: str) -> Transition:
    """Check, then write. The single path a status may change through."""
    check(entity, status)
    before = entity.status
    entity.set_status(status)
    return Transition(entity=entity.id.text, before=before, after=status)
