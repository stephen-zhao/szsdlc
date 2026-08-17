# szsdlc

A project-agnostic agentic SDLC framework, distributed as a Claude Code plugin
plus a CLI.

Every fact about work is stated **once**, in a machine-readable record. Every
index, board, roadmap, back-link and cross-reference is **generated**. The
deterministic operations — what is next, what changed, is this consistent — are
script calls with fixed token cost rather than model reasoning over prose.

> **Status: scaffold.** Nothing is implemented yet. The design is complete and
> reviewed; see the plan below.

## Why

Measured evidence, not vibes:

- Specification discipline — not model capability — is the binding constraint
  on AI-assisted work, yet heavyweight spec frameworks cost more in human
  review than they return.
- The dominant token cost in multi-agent pipelines is **re-injecting the same
  standards** into every invocation.
- Encoding institutional knowledge as machine-consumable units rather than
  prose measurably reduces friction.

So: state facts once, generate everything derived, inject standards only where
they apply, and keep prose volume *down* rather than up.

See [`docs/research.md`](docs/research.md) for the sources and the framework
comparison that led here.

## Model

| Concept | What it is |
|---|---|
| **Entity** | Anything trackable, with an opaque id (`WI-0042`). Frontmatter is the only hand-authored state |
| **Relations** | Typed, cross-entity, authored on one side only — inverses are generated |
| **Roadmap** | One record of horizon buckets; the single home for priority and placement |
| **Views** | Generated markdown: inbox, board, roadmap, rollups, registers, traceability |
| **Records** | A project-defined dataset rendered through a template |
| **Standards** | Conventions with `applies_to` globs, injected only when a matching file is edited |

Entity types are configuration, not code. Idea, epic, requirement, spike, work
item and decision ship as replaceable defaults, each declaring capability flags
(`actionable`, `tracks_progress`, `can_parent`, `schedulable`, `persistent`)
so behaviour follows declared capability rather than a hardcoded type name.

## Documentation

- [`docs/plan.md`](docs/plan.md) — the implementation plan, task by task
- [`docs/research.md`](docs/research.md) — landscape research, effectiveness
  evidence, and the four framework options that were weighed

## Development

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"   # POSIX: .venv/bin/pip
.venv/Scripts/pytest                    # POSIX: .venv/bin/pytest
```

## License

MIT — see [`LICENSE`](LICENSE).
