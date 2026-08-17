# Example: a research lab

This project exists to demonstrate one claim: **no module in `szsdlc` names an
entity type.** Behaviour follows the capability flags a type declares, so a
configuration with nothing in common with the shipped defaults still gets
working views, working gates, working scheduling and working traceability.

There is no software in this project. It is a wet lab.

## What it declares

| | Shipped defaults | This project |
|---|---|---|
| Intake | `idea` | `question` |
| Bundle | `epic` | `programme` |
| Definition | `requirement` | `hypothesis` |
| Investigation | `spike` | *(none)* |
| Work | `work_item` | `experiment` |
| Reference | `decision` | `protocol` |
| Horizons | now / next / later | this-quarter / next-quarter / someday |
| Coverage relation | `implements` | `tests` |
| Parent relation | `parent` | `programme` |

Five types, not six. Different workflow shapes, with different numbers of
states and different gates: an experiment cannot reach `running` without a run
sheet, or `written-up` without results and every run ticked off.

`entity_types`, `relations`, `roadmaps` and `views` each use `_replace: true`,
which throws the shipped block away rather than merging over it. Without that,
this lab would inherit six types it has no use for.

## What that demonstrates

- **The flags carve at the joint.** `hypothesis` lands on exactly the flags the
  shipped `requirement` does — persistent, never scheduled, never worked, no
  progress — in a domain that has never heard of a requirement. So does
  `protocol`. That the same four flags describe a software requirement and a
  bench protocol is the evidence that they are the right four.
- **Derived facts are declared, not hardcoded.** `hypothesis` declares
  `covered` and `delivered` over its own `tests` relation. The register view
  selects types by `persistent` plus a `derived` block and renders whatever
  columns the type declares — so nothing in the framework knows what coverage
  *is*, only how to compute it.
- **The views are shipped, not copied.** `hypothesis-register.md` and
  `bench-schedule.md` are rendered by the same bundled templates that produce
  the software-flavoured ones. Only one file here is project-specific: the
  instrument-calibration record's template.
- **Records take arbitrary project data.** `overview/instruments.yml` is
  hand-authored, validated against a project-supplied JSON Schema, and rendered
  through a project-supplied template into `overview/instruments.md`.

## Running it

```bash
szsdlc -C examples/research-lab context
```

Everything under `overview/` is generated — the banner at the top of each file
says so, and `szsdlc validate` fails if any of it is stale or hand-edited.
`tests/test_example.py` asserts exactly that, which makes this directory a
canary: change a shipped template and the suite fails until it is re-synced,
so the committed sample can never drift into showing output the tool no longer
produces.
