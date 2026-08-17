# Spike: Agentic SDLC Framework

**Date:** 2026-08-16
**Status:** Decided — Plan C selected, see [`plan.md`](plan.md)

Research into agentic SDLC frameworks and four candidate plans, conducted in
the `s-s1-lab` homelab infrastructure repo whose ad-hoc docs workflow prompted
it. That repo became the framework's first intended consumer; §1 below is its
problem statement, retained because it is the concrete evidence the design
answers to.

---

## 1. Problem statement (from the originating project)

The workflow observed there (refinement loop → development loop, `docs/backlog.md` +
`docs/prioritization.md` + `docs/superpowers/{specs,plans}/`) has produced good
artifacts but has no machine-readable state. Concrete weaknesses:

| # | Weakness | Evidence in repo |
|---|---|---|
| W1 | No status on any work item | Determining "caching layer is 10/13 tasks done" required reading `git log` |
| W2 | Plan checkboxes are write-once | `2026-04-15-caching-layer.md` — every box unchecked, 10 tasks demonstrably done |
| W3 | Spike outputs split across two locations | `docs/spikes/` (1 file) vs `docs/superpowers/specs/` (21 files) with no rule |
| W4 | No traceability chain | backlog item → spike → spec → plan → branch → PR → merge is manual every hop |
| W5 | Completed work stored as prose | `prioritization.md` "Completed:" is a comma list, no dates, no PR links |
| W6 | ADRs buried as untitled subsections | `concepts.md` §Architecture Decisions — not addressable, not dated, not immutable |
| W7 | No dev↔prod parity ledger | The actual gate for the migration goal is untracked |
| W8 | Living docs drift silently | `README.md` still lists pre-PR#2 role names and playbooks |
| W9 | Ordering requires re-derivation | "What's next" means reading two long prose docs and reasoning about deps |

W1–W5 and W9 are all the same root cause: **state is stored in prose, so
reading or updating it costs model tokens and judgment instead of a script
call.**

---

## 2. Research: what exists

### 2.1 Landscape

| Framework | Model | Unit of work | Artifacts/change | Maturity (reported) |
|---|---|---|---|---|
| [GitHub Spec Kit](https://github.com/github/spec-kit) | Sequential pipeline: `constitution → specify → clarify → plan → tasks → analyze → implement → checklist` | Feature | 7+ files | ~93k stars (May 2026), 30+ agents supported, 138 community extensions |
| [OpenSpec](https://github.com/Fission-AI/OpenSpec) | Delta cycle: `propose → apply → archive` | Change (delta) | ~4 files | Brownfield-first, no API key/MCP, npm install |
| [BMAD-METHOD](https://github.com/bmad-code-org/BMAD-METHOD) | 4 phases (Analysis/Planning/Solutioning/Implementation), 12+ role agents, scale-adaptive routing | Story | Many | ~46.7k stars, v6.8.0 (May 2026) |
| [Amazon Kiro](https://kiro.dev) | Agentic IDE, spec-first | Feature | — | AWS-oriented, IDE lock-in |
| [Agent OS v3](https://github.com/buildermethods/agent-os) | Standards `discover → index → inject`, then spec shaping | Standard + spec | — | Tool-agnostic, claims 200–500 tokens per injection |
| [SpecOps](https://github.com/dotlabshq/spec-ops) | Spec Kit adapted to Terraform/Ansible/ArgoCD | Infra change | 4 | **8 stars, no forks — immature** |
| [obra/superpowers](https://github.com/obra/superpowers) | Skills-as-methodology (brainstorm → plan → execute-plan, TDD, subagent review) | Plan | 2 (spec+plan) | Already partially in use here — `docs/superpowers/` came from it |
| [AGENTS.md](https://agents.md) | Instruction file convention, not a workflow | — | 1 | 60k+ repos; donated to Linux Foundation Agentic AI Foundation, Dec 2025 |

### 2.2 Effectiveness evidence

This is the part worth reading carefully — the marketing and the measurements
disagree.

**Against unstructured AI assistance:**

- **METR RCT** (Jul 2025): 16 experienced OSS developers, 246 real tasks in
  repos they averaged ~5 years on, randomized AI-allowed vs not. Result:
  **19% slower with AI**, while the same developers estimated they had been
  **20% faster**. Forecast beforehand was +24%. METR now labels the result
  historical (early-2025 tooling). Still the only RCT of its kind.
- **DORA, State of AI-assisted Software Development (2025)**: AI acts as an
  **amplifier** of existing organizational capability. "The greatest returns on
  AI investment come not from the tools themselves but from … the quality of
  the internal platform, the clarity of workflows." Hidden costs named:
  verification overhead, skill degradation, integration friction.

**For specification discipline:**

- **The Productivity-Reliability Paradox** (arXiv 2605.01160): multivocal
  review of 67 sources 2022–2026 plus a 4-month pilot of two specification
  governance implementations (Spec Kit and TDAD). Reported spread: **20–56%
  gains on well-scoped tasks**, **19% slowdown for experienced devs**, and
  telemetry across **10,000+ developers showing +98% PRs opened but +91%
  longer review times with flat delivery metrics**. Thesis: *"Specification
  discipline, not model capability, is the binding constraint on AI-assisted
  software dependability."*
- **Knowledge Activation** (arXiv 2603.14805): Yahoo deployment, engineer
  survey n=67. Encoding institutional knowledge as "Atomic Knowledge Units" —
  actionable, machine-consumable units rather than prose docs — reported
  **2.6 hours/week saved** and **NPS +35**.

**Against heavy ceremony:**

- **Scott Logic trial of Spec Kit** (Nov 2025): rebuilding one CRUD feature
  produced **2,577 lines of markdown for 689 lines of code**; **33.5 min of
  agent time plus 3.5 hours of human review** for work the author estimated at
  ~8 minutes of iterative prompting. Named failure modes: waterfall dynamics,
  forced context-switching, over-specification ("faux context"), and genuine
  ambiguity about whether a one-line bug fix should re-enter the pipeline.
- **BMAD token analysis**: a mid-complexity feature through the full 8-agent
  pipeline ≈ **720k input / 149k output tokens (~$7.30 at Opus 4.8 pricing)**,
  with **~80%+ of spend attributed to re-injecting the same standards
  documents into every agent invocation**.

### 2.3 Requirements modelling (second research round)

Prompted by a review question on whether requirements and epics are the same
thing. They are not, and the distinction changes the model:

- **Epics are containers for work, not specifications.** [Agile Alliance](https://agilealliance.org/epic-confusion/)
  frames an epic as a placeholder for a conversation and a strategic container
  spanning long timeframes — deliberately light on detail, optimised for
  communication over specification.
- **Requirement coverage is computed from traceability links, not stored.**
  In requirements-engineering tooling aligned to the standard, a requirement's
  coverage and verification status are derived by walking satisfaction links
  (what implements it) and verification links (what proves it). Gaps are found
  by querying for *absent* links — filters of the form "NOT is satisfied by" —
  rather than by reading a status field.
- **Definition status is stored; delivery status is derived.** The same tooling
  does carry a `Status` attribute on a requirement, but it describes the
  statement's own lifecycle (draft, approved), not whether work against it has
  landed.

**Citation accuracy:** the applicable standard is **ISO/IEC/IEEE 29148:2018
(Edition 2**, published November 2018, reviewed and confirmed 2024, current).
The 2011 first edition is **withdrawn** and must not be cited. The standard is
paywalled, so the three points above are evidenced by 29148-aligned tooling
practice and secondary sources, **not** verified against normative clauses of
the standard text.

### 2.4 What the evidence implies for *this* repo

1. **Spec discipline is worth keeping; ceremony is not.** Gate on *decisions*
   (design approved, plan approved), not on *document production*.
2. **Re-injection is the dominant token cost.** Standards and context must be
   indexed and injected on demand — not pasted into every turn. This is
   Agent OS's whole thesis and it matches the BMAD cost breakdown.
3. **Review time is the real bottleneck** (+91%). Generating *more* prose for
   a solo operator to review is a direct regression. Prefer machine-checkable
   state over narrative status.
4. **Platform quality > prompt quality** (DORA). Investment belongs in scripts
   and validation, not in longer instructions.
5. **Brownfield/delta beats greenfield/full-spec.** This repo is mid-migration
   with 26 merged PRs of history. Spec Kit and BMAD are greenfield-oriented.
6. **Prefer conventions with gravity.** AGENTS.md (60k repos, LF-governed) and
   MADR/log4brains ADR conventions cost nothing to adopt and buy tool
   portability.

---

## 3. Design principles for the new framework

Derived from §2.4 plus the user requirements (DRY, script the deterministic,
single communication of each fact):

- **P1 — One fact, one home.** Every fact about a work item lives in exactly
  one place. Everything else (indices, boards, roadmaps, backlogs, dep graphs,
  "completed" lists, cross-links) is **generated**.
- **P2 — Generated files are read-only to humans and agents.** Marked, and
  enforced by a hook.
- **P3 — Deterministic steps are shell, not thinking.** "What's next", "what
  changed", "is this consistent", "renumber priorities", "update the index"
  are script calls with fixed token cost.
- **P4 — Judgment steps are skills.** Skill bodies load only when used, so
  procedure text costs nothing until invoked.
- **P5 — State is machine-checkable.** Status transitions validate; a plan's
  checkbox state is parsed, not narrated.
- **P6 — Gates are hooks, not etiquette.** Enforcement that depends on the
  agent remembering is not enforcement.
- **P7 — Prose stays short and human.** Target: fewer markdown lines per change
  than the Scott Logic experience, not more.

---

## 4. Candidate plans

Four plans, ordered from most-external to most-native. All four assume the
framework is built first and content migrated afterwards.

### Plan A — Vendor OpenSpec, wrap with local scripts

Adopt [OpenSpec](https://github.com/Fission-AI/OpenSpec) as the workflow
engine. Its `propose → apply → archive` cycle and delta-spec model are the
closest external fit for brownfield infra work.

**Layout**

```
openspec/
  project.md                 # conventions (replaces part of CLAUDE.md)
  specs/<capability>/        # current truth, updated on archive
  changes/<change-id>/       # proposal.md, tasks.md, delta specs
  changes/archive/
docs/                        # living reference only
```

**Adds locally:** a `lab` script for the refinement loop (backlog/priority/
parity), because OpenSpec covers change execution but not idea triage.

| Pros | Cons |
|---|---|
| Least invention; upstream maintains the workflow | npm/TypeScript runtime in a Python + Ansible + Make repo |
| Brownfield-first, delta-based, explicit archive step | "Capability specs" model fits apps better than VM/stack/runbook infra |
| ~4 files/change — light | Refinement loop (W7, W9) still needs custom scripts, so it's a hybrid anyway |
| Tool-agnostic | Upstream churn; framework decisions not ours |

**Effort:** ~1 day setup, ~2 days for the local refinement wrapper.
**Risk:** medium — two overlapping systems (OpenSpec state + local state).

---

### Plan B — Spec Kit / SpecOps formal pipeline

Adopt Spec Kit (or the IaC-flavored SpecOps fork) and map the two existing
loops onto `constitution → specify → clarify → plan → tasks → analyze →
implement`.

**Layout**

```
.specify/memory/constitution.md    # architecture principles from CLAUDE.md
.specify/specs/<NNN-feature>/      # spec.md, plan.md, tasks.md, research.md, checklist.md
```

| Pros | Cons |
|---|---|
| Largest ecosystem (~93k stars), 30+ agents, extensions like CI Guard | Measured ceremony cost: 2,577 md lines / 689 code lines; 3.5 h review for one feature |
| `constitution` maps cleanly onto this repo's Architecture Principles | Greenfield-oriented; awkward for one-line fixes and ops runbooks |
| Community-maintained templates and analysis commands | Directly contradicts P7 and the +91% review-time finding |
| SpecOps proves the IaC mapping is possible | SpecOps itself is 8 stars — effectively unmaintained; adopting means owning a fork |
| | Still no status model, parity ledger, or generated indices — W1/W5/W7/W9 unsolved |

**Effort:** ~1 day setup, ongoing per-change overhead is the real cost.
**Risk:** high for a solo operator — this is the option the evidence argues against.

---

### Plan C — Native framework: item-as-record + generated everything ★ recommended

Build a purpose-fit framework on Claude Code primitives (skills, hooks) and
this repo's existing toolchain (Make + Python venv + SOPS), borrowing *ideas*
rather than runtimes: OpenSpec's delta/archive cycle, Agent OS's
discover-index-inject for standards, MADR/log4brains for ADRs, AGENTS.md for
the tool-agnostic entry point.

**Single source of truth:** one directory per work item. Frontmatter is the
record; markdown is the prose; everything else is generated. Paths, item types,
workflow and views are all project configuration — nothing below is fixed.

```
<entities>/WI-0042-some-work-item/
  entity.md        # ← THE record. YAML frontmatter + brief. Hand-written.
  spike.md         # optional, only if status passed through `spike`
  design.md        # optional
  plan.md          # task list; checkbox state is PARSED, not narrated
  journal.md       # append-only, written only by `szsdlc log`
<views>/
  index.md         # GENERATED — all items, status, progress %
  board.md         # GENERATED — grouped by status
  roadmap.md       # GENERATED — priority order + mermaid dependency graph
  done.md          # GENERATED — terminal items w/ dates + links
<decisions>/
  ADR-0001-some-decision.md      # MADR format, immutable once accepted
  index.md         # GENERATED
<records>/         # project-defined YAML dataset + template → generated table
<standards>/       # atomic, indexed, injected on demand (Agent OS pattern)
```

**Records** are the generic escape hatch: any project-specific tracked table
(for this repo, a dev↔prod parity ledger) is declared as a YAML dataset plus a
template, so W7 is closed without the framework knowing what a "parity" or an
"environment" is.

`entity.md` frontmatter carries every fact that today requires prose or git
archaeology:

```yaml
---
id: WI-0042
title: Caching layer (Valkey + Sentinel)
status: executing        # from this entity type's configured workflow
priority: 1
relations:
  implements: [REQ-0007]   # inverse `implemented_by` is GENERATED on REQ-0007
  depends_on: [WI-0038]
  informed_by: [SPK-0003, ADR-0012]
links: {branch: spike/caching-layer, pr: null}
opened: 2026-04-15
---
```

Core fields only; a project declares any extra fields it needs in config (this
repo would add one linking an item to the parity capabilities it unblocks).

**The `szsdlc` CLI** — every deterministic operation becomes a fixed-cost call
instead of a search-and-reason:

| Command | Replaces | Approx token cost |
|---|---|---|
| `szsdlc next` | reading backlog.md + prioritization.md and reasoning about deps | ~200 tok |
| `szsdlc show WI-0042 --context` | globbing for the right spec + plan + status | ~1–3k tok, scoped |
| `szsdlc new/set/link/log` | hand-editing prose in 3 files | 0 (script) |
| `szsdlc render` | manually updating indices, renumbering priorities, moving items to "Completed" | 0 (script) |
| `szsdlc validate` | nothing today — inconsistency is silent | 0 (script) |
| `szsdlc new ADR` | hand-writing an ADR section into concepts.md | 0 (script) |
| `szsdlc render` (records) | nothing today (W7) | 0 (script) |

`szsdlc validate` is where W1–W9 actually get closed: schema-validate frontmatter,
detect broken `[[links]]` and dangling `depends_on`, flag status/checkbox
disagreement (a `done` item with unchecked tasks, or an `executing` item whose
plan is 100% checked), flag items whose `branch` is merged but `status` isn't
`done`, and flag generated files that are stale relative to their sources.

**Gates as hooks** (verified against the Claude Code hooks reference):

| Hook | Action |
|---|---|
| `SessionStart` | inject output of `szsdlc context` — the agent starts already knowing the state |
| `PreToolUse` (Edit/Write) | exit 2 on any write to a file marked `<!-- GENERATED -->` |
| `PostToolUse` (Edit/Write on items/records) | run `szsdlc render` — views can never be stale |
| `Stop` | run `szsdlc validate`; block with the reason on failure |
| git `pre-commit` | same validation for non-agent commits + CI |

**Skills as the procedure carriers** (bodies load only on use, per the skills
docs): `/refine`, `/spike`, `/design`, `/plan-item`, `/execute-item`,
`/close-item`, `/adr`. Each skill is deliberately short: call `lab` for state,
apply judgment, call `lab` to record. The two existing loops in CLAUDE.md
survive unchanged — they just become executable.

| Pros | Cons |
|---|---|
| Closes every weakness W1–W9 by construction | We build and maintain it (~est. 3–5 days) |
| Zero new runtimes; uses Make + venv already present | No upstream community to inherit fixes from |
| Token cost of "where are we" drops from a multi-file search to one script call | Requires discipline to keep skills short |
| Fits infra work units (VM, stack, runbook, migration) rather than app features | Custom = onboarding cost if ever shared |
| Prose volume goes *down*, addressing the review-time finding | |

**Effort:** ~3–5 days. **Risk:** low-medium; entirely reversible, all plain files.

---

### Plan D — Issue-tracker-native (GitHub Issues + Projects as the record)

Make GitHub the state store. Items are Issues with typed fields; docs in-repo
are generated from the API; PRs auto-close items.

| Pros | Cons |
|---|---|
| Real state machine, zero index scripts to write | Every agent lookup is a network + `gh` call, not a local read |
| PR/branch/commit linkage is automatic (closes W4 free) | Homelab work is frequently offline/air-gapped |
| Boards, filters, dependency fields all exist | Prose and state separate again — the exact W1 split, inverted |
| Notifications and history for free | Vendor lock-in for the process layer of an anti-lock-in IaC repo |
| | Doesn't solve W6/W7 (ADRs, parity) — still need in-repo files |

**Effort:** ~1–2 days. **Risk:** medium — couples the SDLC to network availability.

---

## 5. Comparison

| Criterion | A: OpenSpec | B: Spec Kit/SpecOps | **C: Native** | D: GitHub-native |
|---|---|---|---|---|
| Closes W1 (status) | partial | no | **yes** | yes |
| Closes W4 (traceability) | partial | no | **yes** | yes |
| Closes W5/W9 (generated indices, `next`) | no | no | **yes** | partial |
| Closes W6 (ADRs) | no | no | **yes** | no |
| Closes W7 (parity ledger) | no | no | **yes** | no |
| DRY / single communication | partial | no | **yes** | partial |
| Deterministic steps scripted | partial | partial | **yes** | yes |
| Token cost per change | low | **high** | **lowest** | medium |
| Prose volume per change | low | **very high** | **lowest** | low |
| Fits brownfield infra | good | poor | **best** | good |
| New runtime dependency | npm | uv/py + fork | **none** | `gh` + network |
| Build effort | 3 d | 1 d + ongoing tax | **3–5 d** | 1–2 d |
| Evidence support | good | **contradicted** | good | neutral |

**Recommendation: Plan C**, with two adoptions from outside: AGENTS.md as the
tool-agnostic instruction entry point (CLAUDE.md becomes a thin pointer), and
MADR-style dated ADR files with a generated log.

Rationale: the measured failure mode of external SDD frameworks is ceremony
cost paid in human review time, which is precisely the scarce resource for a
solo operator. The measured *win* is specification discipline plus indexed,
on-demand knowledge — both of which Plan C keeps while generating less prose,
not more. Plan C is also the only option that closes W6 and W7, and W7 is the
gate on the actual goal of this repo.

---

## 6. Decision

**Plan C selected**, with these decisions taken on 2026-08-16:

| Decision | Choice |
|---|---|
| Name | `szsdlc` — repo, plugin and CLI |
| Scope | **Project-agnostic.** Nothing specific to this lab — no environments, no dev/prod, no infrastructure vocabulary. Project shape lives in config and templates |
| Location | New standalone repo at `E:\Projects\Dev-kyle\szsdlc` |
| Distribution | Claude Code plugin listed in the existing `szccpmp` marketplace. A marketplace entry may reference a plugin in a different repo via a `github` source, pinned independently — **no submodule needed** |
| Identifiers | **Opaque: `<TYPE>-<NNNN>`**, one counter per type, one form everywhere. Established practice is that ids carry no decodable meaning, since anything embedded that can change invalidates published references |
| Entity types | Configurable. Epic, requirement, spike, work item and decision ship as replaceable defaults, each with its own prefix, schema and workflow |
| Grouping | Two independent metadata axes: **tags** (free-form, many per item) and **epic** (toward what outcome, via a `parent` relation). Neither is an ID segment |
| Reclassification | `szsdlc convert` mints the new id, links `supersedes`, and leaves a resolving tombstone — the accepted cost of putting type in the ID |
| Relations | Typed and cross-entity, authored on **one side only**; inverses are generated |
| Task tracking | Markdown checkboxes, parsed for progress |
| Git automation | Deferred past v1 to keep the framework forge-agnostic |
| Journal | Kept, written only by `szsdlc log` |

Implementation plan: [`plan.md`](plan.md).

Adoption in this repo, and migration of the 24 plans, 21 specs, backlog,
prioritization and `concepts.md` ADRs, are **separate work items** after the
framework is proven elsewhere.

## Sources

- [METR, Measuring the Impact of Early-2025 AI on Experienced Open-Source Developer Productivity](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/)
- [DORA, State of AI-assisted Software Development 2025](https://dora.dev/dora-report-2025/)
- [The Productivity-Reliability Paradox (arXiv 2605.01160)](https://arxiv.org/abs/2605.01160)
- [Knowledge Activation: AI Skills as the Institutional Knowledge Primitive (arXiv 2603.14805)](https://arxiv.org/abs/2603.14805)
- [Scott Logic, Putting Spec Kit Through Its Paces: Radical Idea or Reinvented Waterfall?](https://blog.scottlogic.com/2025/11/26/putting-spec-kit-through-its-paces-radical-idea-or-reinvented-waterfall.html)
- [GitHub Spec Kit](https://github.com/github/spec-kit) · [docs](https://github.github.com/spec-kit/)
- [OpenSpec](https://github.com/Fission-AI/OpenSpec) · [Hashrocket comparison](https://hashrocket.com/blog/posts/openspec-vs-spec-kit-choosing-the-right-ai-driven-development-workflow-for-your-team)
- [BMAD-METHOD](https://github.com/bmad-code-org/BMAD-METHOD) · [token budget analysis](https://reenbit.com/bmad-method-token-budget-context-engineering-roi/)
- [Agent OS](https://buildermethods.com/agent-os) · [v3 discussion](https://github.com/buildermethods/agent-os/discussions/310)
- [SpecOps](https://github.com/dotlabshq/spec-ops)
- [obra/superpowers](https://github.com/obra/superpowers)
- [AGENTS.md guide](https://www.morphllm.com/agents-md-guide)
- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks-guide) · [skills reference](https://code.claude.com/docs/en/skills)
- [log4brains / MADR ADR tooling](https://adr.github.io/adr-tooling/)
- [Identifiers should not contain semantics — except when they should](http://ptsefton.com/2007/07/13/12:03:16.154030/index.html) — embedded meaning invalidates published references when it changes
- [ISO/IEC/IEEE 29148:2018 — Requirements engineering, Edition 2](https://www.iso.org/standard/72089.html) (current; the 2011 first edition is withdrawn)
- [ReqView, Requirements Traceability Matrix for Systems Engineers](https://www.reqview.com/blog/requirements-traceability-matrix/) — coverage derived from links, gaps found via absent-link queries
- [Agile Alliance, Epic Confusion](https://agilealliance.org/epic-confusion/) — epics as containers for work, not specifications
