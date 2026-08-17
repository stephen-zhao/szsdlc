---
name: trace
description: Answer questions about how work connects — what satisfies a requirement, where a work item came from, what a change would break, or what is blocking something. Use when someone asks why does this exist, what covers this, or what depends on this.
---

# Trace

Every relation is authored on one side and its inverse generated, so the
graph answers questions in both directions without anyone maintaining a
back-reference.

```bash
szsdlc trace REQ-0004              # both directions, depth 2 by default
szsdlc trace WI-0042 --depth 4     # further out
szsdlc show REQ-0004 --context     # the entity plus its immediate neighbourhood
```

## Reading the answer

Coverage and delivery are **derived**, never stored: a requirement is
covered because something implements it, and delivered because everything
implementing it has finished. Nothing is written to it when work starts, so
`trace` shows current truth rather than the last time someone remembered to
update a field.

`(missing)` beside a target means the edge points at something that is not
there. Report it — do not silently drop it from your answer. A dangling
reference is held exactly as written so it stays findable.

## The questions this answers

- **"What satisfies this requirement?"** — trace the requirement; the
  `implemented_by` side lists it. `szsdlc list --uncovered` finds the ones
  nothing satisfies at all.
- **"Where did this come from?"** — trace back through provenance to the
  original idea. This makes "why are we building this" answerable later.
- **"What breaks if I change this?"** — follow the blocks direction. Depth 2
  is usually the useful radius; deeper is for arguing about a rewrite.
- **"Why is this blocked?"** — the depends-on side, and whether those are
  terminal yet.

## Answering well

Lead with the answer in a sentence, then the evidence. "REQ-0004 is covered
by WI-0042, which is executing at 4/9" is what someone asked for; a dump of
twenty edges is what they have to read to get it themselves.

If the trail is genuinely broken — nothing implements an approved
requirement, provenance stops at nothing — say so plainly. That gap is the
finding, and `szsdlc validate` will be reporting it too.
