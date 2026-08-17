---
name: spike
description: Run a timeboxed investigation to an answer and write it down. Use when a question is blocking a design or an estimate, when someone asks which of these should we use, or when a work item cannot proceed until something is measured or tried.
---

# Spike

A spike buys an answer, not a feature. Its output is a written finding that
outlives the investigation; anything you build along the way is scaffolding.

```bash
szsdlc show SPK-0003 --context     # the question, and what depends on it
szsdlc set SPK-0003 status=researching
```

## Before starting

Confirm the question is written down and answerable. If the frontmatter has
no `question`, write one first:

```bash
szsdlc set SPK-0003 question="Can we stream 10k rows without the client stalling?"
```

A question you cannot imagine an answer to is not a spike, it is a wish.
Rewrite it until a specific finding would settle it, and agree a bound —
hours, or an amount of code — out loud before starting.

## Working

Log what you learn as you learn it, so an interrupted spike is not a total
loss: `szsdlc log SPK-0003 "server-side cursors: 10k rows in 240ms"`.

## Finishing

Write `findings.md` in the spike's directory (`szsdlc show SPK-0003 --json`
reports the path). It states **the answer**, the evidence for it, and what
you would do next — not a diary of what you tried.

```bash
szsdlc set SPK-0003 status=answered
```

The transition is refused until the findings artifact exists; that refusal
is the point of the gate, not an obstacle to route around.

An answer of "no" or "we cannot tell yet" is a complete spike. Record it and
close. What must never happen is a spike that quietly becomes the feature:
if you find yourself building the real thing, stop, answer the question, and
let a work item carry the build.
