---
name: execute
description: Work the current plan task by task, ticking boxes and logging as you go. Invoke explicitly when you want work carried out against a planned item.
disable-model-invocation: true
---

# Execute

One task at a time, in plan order. The plan's checkboxes are the project's
progress signal; they are only worth reading if they are ticked when the
work is actually done and not before.

```bash
szsdlc context                     # in flight, and the current task
szsdlc show WI-0042 --json         # the path; plan.md sits beside entity.md
```

## The loop

For each unchecked task, in order:

1. Do the work. Not the next three tasks — this one.
2. Verify it the way the task says it is observable: run the test, run the
   command, look at the output. A task is not done because the edit applied.
3. Tick its box in `plan.md`.
4. Log one line, if a future reader would want it:
   `szsdlc log WI-0042 "cursor batching landed; 10k rows now 240ms"`.

Stop between tasks. That is where someone can redirect you cheaply, and
where a wrong direction has cost one task rather than ten.

## When the plan is wrong

It usually is, somewhere. Editing the plan is normal work, not a failure:
add the task you discovered, split the one that turned out to be three,
delete the one that is no longer needed. What is never acceptable is ticking
a box for work that did not happen, or leaving discovered work unwritten
because it is not on the list.

If the discovery invalidates the *approach* rather than a task, stop
executing and say so. That is a design conversation, not a plan edit.

Something out of scope turns up: capture it and keep going —
`szsdlc capture "retry logic double-counts on timeout"`.

## Finishing

When every box is ticked, hand off to `/szsdlc:close`. Do not set a terminal
status here — closing has its own verification, and skipping it is how work
gets marked done twice and finished never.
