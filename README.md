# szsdlc

A project-agnostic agentic SDLC framework, distributed as a Claude Code plugin
plus a CLI.

Every fact about work is stated **once**, in a machine-readable record. Every
index, board, roadmap, back-link and cross-reference is **generated**. The
deterministic operations — what is next, what changed, is this consistent — are
script calls with fixed token cost rather than model reasoning over prose.

```bash
szsdlc capture "users keep losing unsaved drafts"     # → IDEA-0007
szsdlc refine IDEA-0007 --into work_item --title "Autosave drafts"
szsdlc schedule WI-0042 --horizon now --after WI-0031
szsdlc next                                           # what to do, in order
szsdlc context                                        # the whole project, ~33 tokens
```

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

## What it costs

Measured, not asserted — see [`docs/measurements.md`](docs/measurements.md) for
method and the full table.

| | 20 entities | 200 entities |
|---|---:|---:|
| `szsdlc context` — the whole project state | **33 tokens** | **33 tokens** |
| the unscheduled listing its counter replaces | 100 | 1000 |

The counter is flat and the listing is linear, so the saving grows with exactly
the thing that makes a project hard to hold in context. Full-graph load is
0.13 s at 200 entities and 1.04 s at 2000; there is no index cache, because a
stale cache is the failure mode this framework exists to eliminate and 0.13 s
is not pain.

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
(`intake`, `actionable`, `tracks_progress`, `can_parent`, `schedulable`,
`persistent`) so behaviour follows declared capability rather than a hardcoded
type name.

Three properties fall out of that, and they are the ones worth knowing:

- **Derived facts are never stored.** Whether a requirement is covered, whether
  it is delivered, how far an epic has got — all computed from the graph when
  read. Finishing work against a requirement changes not one byte of the
  requirement's file.
- **Priority is placement.** There is no `priority` field. An item is not
  urgent in isolation; it is ahead of the others, and that is a fact about the
  roadmap.
- **Invalidity is representable.** An unparseable entity, a dangling reference,
  a status outside the workflow — all load and get *reported* rather than
  raising, so a broken project still renders and can be repaired.

[`examples/research-lab`](examples/research-lab) is the proof that none of this
is software-specific: a wet lab with five entity types, six relations and three
horizons, none of them shared with the defaults, rendered by the same shipped
templates.

## Install

As a Claude Code plugin — this brings the hooks and the ten skills:

```bash
/plugin install szsdlc@szccpmp
```

Or as a plain CLI:

```bash
pip install szsdlc
```

Then, in any project:

```bash
szsdlc init
```

Python 3.12 or later. The plugin's launchers resolve an interpreter on their
own, so no virtualenv setup is needed for the hooks to work.

## Commands

```
init                    scaffold a project: config, directories, an empty roadmap
capture [text]          capture an idea from an argument, stdin or $EDITOR
refine <ref> --into T   spawn a typed entity from an idea
inbox                   unrefined ideas, oldest first
drop <ref> --reason ..  close an idea that went nowhere, with a reason
new <TYPE> --title ..   create a typed entity directly
set <ref> field=value   set scalar fields; enforces the workflow
tag|untag <ref> <tag>   add or remove tags, normalized on write
link|unlink a <r> b     author or remove one edge; inverses are generated
convert <ref> <TYPE>    reclassify, leaving a resolving tombstone
log <ref> [msg]         append a dated line to the journal artifact
schedule <ref> -H h     place on a roadmap; --after/--before/--top
unschedule <ref>        take off the roadmap
next                    actionable work, in roadmap order, unblocked
show <ref> [--context]  one entity, or a budgeted context bundle
context                 in-flight work and the counters block
list [filters]          by type, status, tag, parent, coverage, placement
trace <ref> [--depth]   relations both ways, back to the idea
standards match <path>  conventions governing these paths
sync                    regenerate every view and record; silent, never validates
validate                every consistency rule; silent when clean
```

Every command is designed to be called by an agent. Mutations report the
resulting state in one line, so no confirming read is needed. Refusals are at
most three lines and end in something runnable. Listings are bounded and say so
when they truncate. `--json` is opt-in, because JSON is the more verbose
encoding and must never be what a caller falls into by accident.

Exit codes distinguish the cases a caller would branch on: `2` bad input, `3`
refused, `4` validation failed, `5` configuration error.

## Configuration

`.szsdlc/config.yml` is deep-merged over the shipped defaults, so a project can
adjust one flag, delete a type it does not want (`spike: null`), or replace a
whole section outright with `_replace: true`. The full schema is
[`config.schema.json`](src/szsdlc/schemas/config.schema.json); the shipped
defaults are [`defaults/config.yml`](src/szsdlc/defaults/config.yml), written
as documentation as much as data.

## Hooks

The plugin wires four:

| Event | What it does |
|---|---|
| `SessionStart` | Prepends `szsdlc context` — a session begins knowing the state without searching for it |
| `PreToolUse` | Blocks edits to generated files, and injects the standards governing the file being edited |
| `PostToolUse` | Runs `sync` when an entity or roadmap file changes |
| `Stop` | Runs `validate`, blocking on errors only |

Every hook is a silent no-op in a project without `.szsdlc/config.yml`, so the
plugin is harmless installed globally.

## Documentation

- [`AGENTS.md`](AGENTS.md) — the agent-facing contract, six rules with reasons
- [`docs/measurements.md`](docs/measurements.md) — measured token cost and load time
- [`docs/plan.md`](docs/plan.md) — the implementation plan, task by task, with
  every decision taken along the way recorded against the task that forced it
- [`docs/research.md`](docs/research.md) — landscape research and the four
  framework options that were weighed

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"       # Windows: .venv/Scripts/pip
.venv/bin/pytest                        # Windows: .venv/Scripts/pytest
```

The suite runs on Python 3.12 and 3.14, on Linux and Windows. Line endings are
pinned by `.gitattributes`: the POSIX launcher needs LF, and the bundled
templates need it too, since a generated file's content-hash would otherwise
differ between platforms.

## License

MIT — see [`LICENSE`](LICENSE).
