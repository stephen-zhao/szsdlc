# Reference

The model, the command surface, configuration, hooks, and how to work on the
code. For *why* any of this is shaped the way it is, see
[`research.md`](research.md); for what it costs, [`measurements.md`](measurements.md).

```bash
szsdlc capture "users keep losing unsaved drafts"     # → IDEA-0007
szsdlc refine IDEA-0007 --into work_item --title "Autosave drafts"
szsdlc schedule WI-0042 --horizon now --after WI-0031
szsdlc next                                           # what to do, in order
szsdlc context                                        # the whole project, ~33 tokens
```

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

Each type also declares a `layout`, which names **what one entry occupies** —
so that storage cost matches fidelity, and nothing above this line has to know
which it got:

| `layout` | One entry is | For |
|---|---|---|
| `section` | a section of one shared file, named by a `## <ID> — title` heading | High-volume intake. Ten one-line thoughts should not cost ten files |
| `file` | `<dir>/<ID>-<slug>.md` | A thought that has outgrown a section but carries nothing beside it |
| `directory` | `<dir>/<ID>-<slug>/entity.md`, plus artifacts | Anything with a design, a plan or a journal |
| `dynamic` | any of the three, per entry, and it may be laid out again later | Types whose fidelity is not known at capture time. `idea` ships on this |

A section is *a whole entity document* — the same frontmatter and body the
other two layouts store — with a heading above it carrying the name its
filename would have carried. A heading delimits an entry only if it parses as
an id of that type, so a `## Notes` written inside a body does not split it.
`section_file` names the shared file, and defaults to `index.md` inside the
type's own directory.

`dynamic` follows from that. `szsdlc layout <ref> <layout>` lays an entry out
again — same id, same bytes, same links, because a section is already a whole
document and no relation has ever named a path. It is `convert` for the other
axis: `convert` changes what an entry *is*, `layout` changes what it
*occupies*. A thought arrives as a section (`initial_layout`, default
`section`), earns its own file if it grows, and earns a directory the moment
something has to live beside it; asking to `log` against one gives it that
directory rather than refusing. The new layout is written before the old one is
removed, so an interruption leaves the id claimed twice — which `validate`
reports, with both paths — rather than not at all.

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

[`examples/research-lab`](../examples/research-lab) is the proof that none of
this is software-specific: a wet lab with five entity types, six relations and
three horizons, none of them shared with the defaults, rendered by the same
shipped templates.

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
layout <ref> <LAYOUT>   lay an entry out as a section, file or directory
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
[`config.schema.json`](../src/szsdlc/schemas/config.schema.json); the shipped
defaults are [`defaults/config.yml`](../src/szsdlc/defaults/config.yml),
written as documentation as much as data.

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
