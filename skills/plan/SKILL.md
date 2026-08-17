---
name: plan
description: Turn an agreed design into an ordered checklist of tasks that can be worked one at a time. Use after a design is written, or when asked to break down work that is about to start.
---

# Plan

The plan turns a design into moves. Its checkboxes are the progress signal
the whole project reads, so they have to mean something.

Read `design.md` first — `szsdlc show WI-0042 --json` reports the path, and
artifacts sit beside `entity.md`. If the design does not exist or does not
settle the approach, you are not planning yet: run `/szsdlc:design`.

## What goes in `plan.md`

A flat list of `- [ ]` tasks in the order they should be done. Each task:

- **is one sitting's work.** If a task cannot plausibly be finished in one
  go, split it. Tasks that stay half-done for days make the percentage lie.
- **ends in something observable** — a test passes, a command works, a file
  exists. "Refactor the parser" has no end; "parser accepts CRLF input, with
  a test" does.
- **is orderable.** Put the thing that would invalidate the rest first.
  Cheap-and-uncertain beats expensive-and-safe as an opening move.

Include the unglamorous ones explicitly: migration, docs, the config change,
the test. A task that is not on the list does not get counted, and work that
is not counted is what makes a plan finish at 100% while the feature does
not work.

Do not nest checklists. One level, one meaning: this task is done or it is
not.

## Sizing

Five to fifteen tasks is a plan. Three is a design that did not need one.
Forty means the work item is really an epic — split it and give the pieces a
parent, rather than planning a month in one file.

## Finishing

```bash
szsdlc set WI-0042 status=executing
```

Refused until the plan artifact exists. The progress reported everywhere
else is counted from these boxes, so the plan is now a live document: work
it with `/szsdlc:execute`, and edit it when reality disagrees rather than
ticking a box that did not happen.
