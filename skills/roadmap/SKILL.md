---
name: roadmap
description: Decide what gets worked on next and in what order, and apply it to the roadmap. Use when asked what should we do next, when reprioritising, when new work needs a place, or when the roadmap has drifted from reality.
---

# Roadmap

Placement is the only statement of priority this project makes. There is no
priority field on an entity, because priority is a fact about a *set* — an
item is not urgent, it is ahead of the others.

```bash
szsdlc context                     # counters: what is unscheduled, what is stale
szsdlc list --unscheduled          # waiting for a place
szsdlc next                        # what the current order says to do
```

## Applying changes

```bash
szsdlc schedule WI-0042 --horizon now --after WI-0031
szsdlc schedule SPK-0003 --horizon now --top
szsdlc unschedule WI-0018
```

Order within a horizon is meaningful — it is the sequence `next` reads.
Place by relative position (`--after`, `--before`, `--top`) rather than
rewriting the file, so one change moves one thing.

## The judgment

- **Dependencies bound the order.** `szsdlc trace WI-0042` shows what blocks
  it. Scheduling a blocked item ahead of its blocker is a plan that cannot
  execute.
- **The near horizon is a commitment, the far one is a hope.** If everything
  is in `now`, nothing is. A `now` horizon longer than the team can finish
  in its cycle is the most common way a roadmap stops being read.
- **Uncovered requirements are a real signal.** `szsdlc list --uncovered`
  finds approved definitions nothing implements. Those are commitments
  already made and not yet scheduled.
- **Unscheduled ready work is a symptom, not a queue.** Either it should be
  on a horizon or it should not be ready. Both fixes are cheap; leaving it is
  what makes the count climb until people stop reading it.

## Propose, then apply

Say what you are going to move and why, in a few lines, before moving it.
Reordering is a decision about someone else's week; it should be visible as
a proposal and not arrive as a diff. Once agreed, run the commands and
report the resulting order.
