---
name: decide
description: Record an architectural or process decision with its context, options and consequences. Use when a choice is being made that the project will live with, when someone asks why is it built this way, or when a design settles something bigger than one work item.
---

# Decide

A decision entity exists so the reasoning survives the people. Write one
when reversing the choice would be expensive, or when the next person would
otherwise read it as an accident.

```bash
szsdlc new decision --title "Server-side cursors for large result sets"
szsdlc show ADR-0009 --json        # reports the path to write in
```

## When *not* to write one

Most choices are not decisions. Cheap to reverse, local to one module, or no
serious alternative: it belongs in the code or the design. A log recording
everything is read by nobody — costlier than the one it fails to record.

## What goes in the body

- **Context** — what forced a choice. State the constraint, not the
  conclusion. If nothing forced it, this is not a decision.
- **Options** — the ones genuinely considered, each with its real cost. One
  plausible entry and two straw men forecloses the reopening it pretends to
  invite, which is worse than no list at all.
- **Decision** — what was chosen, in one sentence, in the active voice.
- **Consequences** — what this commits the project to, bad parts included.
  Write it as if the choice turned out wrong; it is the section future
  readers actually need.

## Status and links

Accept it once it is actually agreed — `szsdlc set ADR-0009 status=accepted`.
Not in the same breath as proposing it: the proposed state exists so a human
can disagree. Wire it to what it came from and what it touches:

```bash
szsdlc link ADR-0009 informed_by SPK-0003     # the investigation behind it
szsdlc link WI-0042 informed_by ADR-0009      # the work that follows
```

## Changing your mind

Never edit a decision to say something else, and never delete one. Write the
new one and supersede — `szsdlc link ADR-0014 supersedes ADR-0009`. The old
reasoning is what makes the reversal legible.
