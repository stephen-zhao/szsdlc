---
name: capture
description: Record a passing thought, complaint, or "we should someday…" as a tracked idea without derailing the current conversation. Use whenever someone says note that down, we should, remind me, worth doing later, or voices a frustration with the current system while working on something else.
---

# Capture

Intake is only worth having if it is cheaper than not using it. One command,
no questions, back to what you were doing.

```bash
szsdlc capture "users keep losing unsaved drafts"
```

It prints the new id. Say the id in one short clause and continue the task
you were on. Do not summarise, do not ask what type it should be, do not
offer to refine it now — that is `/szsdlc:refine`, and it happens later, on
purpose.

## Recognising an aside

Capture when someone voices something worth doing that is **not** what they
asked you to do right now:

- "we should really fix that someday", "note that down", "TODO"
- a complaint about the system while working on something unrelated
- a risk or edge case you find mid-task that is out of scope to fix

Do **not** capture:

- the task you are currently doing — that is already tracked
- a step in your own working notes
- anything the person is actively asking you to do in this turn

## Writing the text

The captured text is what a future reader gets. One sentence, in their
words, stating the problem rather than a solution. "Drafts are lost on
refresh" survives; "add autosave" pre-commits to an answer nobody has
judged yet.

If several distinct thoughts arrive at once, capture each separately. One
idea per capture is what makes them refinable independently.

## Duplicates

Before capturing, `szsdlc inbox` if you have not seen it this session. If
the same thought is already there, say so and skip the capture. Two ids for
one thought is worse than none.
