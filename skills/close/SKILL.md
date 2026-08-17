---
name: close
description: Verify that a piece of work is genuinely finished and move it to a terminal status. Invoke explicitly when work looks done and you want it closed out.
disable-model-invocation: true
---

# Close

Closing is a claim: this is finished, and nobody needs to come back to it.
The gates check what is mechanical; below is what they cannot.

```bash
szsdlc show WI-0042 --context
szsdlc trace WI-0042               # what this was supposed to satisfy
```

## Verify before transitioning

Four questions, answered by looking rather than remembering:

- **Does it do what it was for?** Read the requirements it implements — not
  the plan. A plan can be fully ticked and still miss what was asked for.
- **Does it actually run?** Execute the tests and the command a user would
  run. "The code looks right" has never once been sufficient.
- **Is the trail honest?** Boxes unticked for work that happened, ticked for
  work that did not, and work discovered but never written down all have to
  be reconciled *now*, while you still know.
- **What is left over?** Follow-ups belong in the inbox, not in a comment
  nobody reads: `szsdlc capture "retry path still lacks a timeout test"`.

## Transition

```bash
szsdlc set WI-0042 status=done
```

Refused while checkboxes remain unticked. If the refusal is right, go finish
the work. If the remaining tasks are genuinely not needed, delete them from
the plan with a logged reason — do not tick them. Work that should not be
finished is abandoned instead, with the reason recorded; a refusal names the
transitions that are legal from here.

## Afterwards

Run `szsdlc validate`. Closing frequently changes derived state elsewhere —
a requirement becomes delivered, an epic's rollup moves, a blocked item
becomes actionable. Say what moved, in one line. That is the difference
between a tracker and a filing cabinet.
