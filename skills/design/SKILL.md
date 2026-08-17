---
name: design
description: Write the design for a piece of work that is ready to start — the approach, the alternatives rejected, and the risks. Use before planning or implementing anything non-trivial, or when asked how should we build this.
---

# Design

The design answers *how*, once, so the plan and the implementation stop
re-deciding it. Write it for whoever inherits this in six months.

```bash
szsdlc show WI-0042 --context      # what it implements, what blocks it
szsdlc trace WI-0042               # where it came from
szsdlc set WI-0042 status=designing
```

Read the requirements it implements before writing a line. A design that
does not reference what it must satisfy is a guess.

## What goes in `design.md`

Write it in the entity's directory (`szsdlc show WI-0042 --json` reports the
path). Four things, in this order:

- **Approach** — what you are going to build, concretely enough to argue
  with. Name the modules, the data shape, the boundary that moves.
- **Alternatives rejected** — the ones a reasonable person would propose,
  and why not. This is the section that pays for the document; without it
  the same debate reopens on the first review comment.
- **Risks and unknowns** — what could make this wrong. If one is severe
  enough to change the approach, stop and run `/szsdlc:spike`.
- **Impact** — what else has to change: migrations, callers, docs, config.

Keep it proportional. A one-file change deserves five lines, not a template
filled in dutifully; length beyond the change is a cost every reader pays.

## Decisions that outlive this work

If the design settles something the *project* will live with — a protocol, a
dependency, a boundary — that belongs in its own decision entity rather than
buried in one work item's design. Use `/szsdlc:decide`, then
`szsdlc link WI-0042 informed_by ADR-0009`.

## Finishing

`szsdlc set WI-0042 status=planned`, refused until the design artifact
exists. Then `/szsdlc:plan`.
