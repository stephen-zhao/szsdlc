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
render                  regenerate every view and record; silent, never validates
validate                every consistency rule; silent when clean
commit                  render, then record the project locally; no network
reconcile               render, commit, then pull and push the backend's remote
backend [show|reconcile]  where the project is kept; reconcile it now
```

`sync` is still accepted as a **deprecated alias for `render`** — the name it
had up to 0.2.4, and the one the banner in every already-generated file still
prints. It warns on stderr and will be removed. It does not reach the network:
the operation that does is `reconcile`, which never shared the name.

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

The plugin wires five:

| Event | What it does |
|---|---|
| `SessionStart` | Pulls from the [backend](#backends-where-a-project-is-kept)'s remote, if any, then prepends `szsdlc context` — a session begins knowing the state without searching for it |
| `UserPromptSubmit` | Pulls from and pushes to the backend's remote, if any, before Claude answers. Silent unless that failed |
| `PreToolUse` | Blocks edits to generated files, and injects the standards governing the file being edited |
| `PostToolUse` | Runs `render` when an entity or roadmap file changes. Records nothing — the turn is the commit boundary |
| `Stop` | Renders, reconciles with the backend, then runs `validate` — blocking on errors only. Pushes either way |

`Stop` fires when Claude finishes responding — the end of a turn, and the
closest Claude Code has to a post-turn event. Both halves run in the one
handler because each entry under the event is its own process and its own load
of the project.

It is also the turn's one commit boundary:

```
render → reconcile (commit, pull, push)
       → render and reconcile again, if the pull brought anything
       → validate
```

`validate` runs **last, after the push**, and nothing is gated on it: an
unfinished turn is still work, and keeping it on one machine is how it gets
lost, so the commit and the push happen either way and `validate` reports on
what was sent. It is last rather than merely late because the pull is what it
has to judge — the merged state, not the one this machine had before. The
commit sits inside the reconcile and therefore before the pull, because git
cannot rebase a dirty tree, so "commit only once `validate` passes" is not on
offer even if it were wanted.

Rendering runs first so `validate` judges the state the turn leaves behind
rather than blocking on a fix that was about to happen anyway, and so the
commit records current views rather than stale ones. It is also the one render
nobody asked for, so it leaves a hand-edited generated file alone for
`validate` to report: overwritten text cannot be recovered, and the evidence
for that error is the file. `szsdlc render` still overwrites.

Every hook is a silent no-op in a project without `.szsdlc/config.yml`, so the
plugin is harmless installed globally.

### Settings

Declared in [`.claude-plugin/plugin.json`](../.claude-plugin/plugin.json) under
`userConfig`, edited with `/config`, and reaching the hook process as
`CLAUDE_PLUGIN_OPTION_<KEY>`:

| Setting | Default | What it does |
|---|---|---|
| `render_on_turn_end` | `true` | Run `render` as the turn ends, not only when a source file is written. Off, `Stop` is the backend reconcile and `validate` alone |

Renamed from `sync_on_turn_end`, which named the wrong operation: it governed
rendering and never the backend. The old key is still read, for one release, so
a choice already made is not silently dropped.

Not `.szsdlc/config.yml` keys: that file is the project's model, read by the
CLI and by CI, which have no turns to render at.

## Backends: where a project is kept

By default a project lives where `.szsdlc/config.yml` is found above the
working directory. A **backend** can keep it somewhere else instead: another
local directory, or a remote that szsdlc keeps in step automatically. szsdlc
then writes nothing into the directory you are working in.

### Configuring one

Settings are keys in the `env` block of Claude Code's own settings files,
the same files that hold your other Claude Code settings:

| File | Scope |
|---|---|
| `~/.claude/settings.json` | user-wide |
| `<project>/.claude/settings.json` | one project, committed |
| `<project>/.claude/settings.local.json` | one project, personal |

Later rows override earlier ones **key by key**, so a user-wide
`SZSDLC_BACKEND_GIT_CLONE_ROOT` still applies to a project that only overrides
the URL. Inside Claude Code the merged values are already in the environment;
the CLI reads the same files directly, so it behaves the same from a terminal.
A variable exported in the shell overrides every file. `CLAUDE_CONFIG_DIR`
moves the user file, as it does for Claude Code.

`SZSDLC_BACKEND` selects the backend, and each backend reads only its own
`SZSDLC_BACKEND_<TYPE>_*` keys. Keys for backends that are not selected are
ignored, so a git setup can stay configured while one project uses `dir`. An
unknown key in the selected backend's namespace is an error.

| `SZSDLC_BACKEND` | The project lives |
|---|---|
| `workdir` (default) | in the working tree, as without any backend. Set this in a project to opt out of a user-wide backend |
| `dir` | in another local directory, with nothing to reconcile |
| `git` | in a remote git repository, through a hidden clone that is reconciled automatically |

**`dir`**

| Key | Default | Meaning |
|---|---|---|
| `SZSDLC_BACKEND_DIR_PATH` | required | the directory the project's files are written to |

**`git`**

| Key | Default | Meaning |
|---|---|---|
| `SZSDLC_BACKEND_GIT_URL` | required | the remote repository |
| `SZSDLC_BACKEND_GIT_BRANCH` | the remote's default | the branch to track; the first push creates it if the remote lacks it |
| `SZSDLC_BACKEND_GIT_SUBDIR` | the top | where in the repository the project lives |
| `SZSDLC_BACKEND_GIT_CLONE_ROOT` | `~/.claude/szsdlc/git` | the directory clones are made under |
| `SZSDLC_BACKEND_GIT_CLONE_PATH` | `<CLONE_ROOT>/<name>-<hash>` | the exact clone, overriding `CLONE_ROOT` |

`{project}` in a path-like value is replaced with the project directory's
name — the directory holding `.claude/settings*.json`, else the enclosing git
checkout, so a command run from a subdirectory names the same project — and
one directory or repository can hold many projects. Relative paths
resolve against the project for project files, and against the home directory
for the user file.

```json
{
  "env": {
    "SZSDLC_BACKEND": "git",
    "SZSDLC_BACKEND_GIT_URL": "git@github.com:me/tracking.git",
    "SZSDLC_BACKEND_GIT_SUBDIR": "{project}"
  }
}
```

`szsdlc backend` shows the backend in effect and which file each value came
from; credentials embedded in a URL are redacted there and everywhere else it
is printed. `szsdlc init` creates the project wherever the backend keeps it.

An existing clone is used only if it is a clone of `SZSDLC_BACKEND_GIT_URL`
with `SZSDLC_BACKEND_GIT_BRANCH` (or, when unset, the remote's default branch)
checked out; otherwise it is refused rather than repurposed. This is checked
again before every commit and reconcile. A clone that would contain the working tree,
and a `SUBDIR` that resolves outside the clone, are refused too.

### Syncing

With a backend that has a remote, the remote is reached at turn boundaries,
not per change:

| When | What |
|---|---|
| session start, before each prompt, turn end | full reconcile: commit, pull, then push |
| after each edit, and each CLI command Claude runs | nothing — the turn is the boundary |
| `szsdlc reconcile`, `szsdlc backend reconcile` | full reconcile, on demand |
| `szsdlc commit` | render, then commit — no network |
| `szsdlc render`, `szsdlc sync` | nothing — rendering is local by definition |
| a CLI command typed in a terminal | render, then full reconcile if it changed anything, since no turn end follows |

Commands tell the two cases apart by `CLAUDECODE=1`, which Claude Code sets in
the shells it runs commands in.

Everything that records or sends renders first, including the terminal path and
`backend reconcile`. Nothing should commit or push a generated view that no
longer matches the entities beside it — and if the next reconcile never comes,
the remote keeps the stale one.

**A turn of its own work produces one commit.** An edit is not a unit of
history: a turn that touches twelve files is one change to the project, and
committing each write made the log a transcript of Claude's tool calls rather
than a record of what changed. Nothing is at risk in between — the edits are on
disk in the store, and `has_local_changes` reads the working tree, so the next
commit or reconcile sweeps in whatever is sitting there uncommitted. A session
that dies mid-turn leaves its work uncommitted in the store; the next session's
start reconciles it.

A turn that **pulls** someone else's work produces a second commit, for the
redraw: views cannot be rendered from entities that have not arrived yet, so
the sequence is commit, pull, render, commit. Calling `szsdlc commit` by name
mid-turn also adds one, which is the point of asking for it.

For `git`, a commit records everything in the clone; a reconcile commits, rebases
onto the remote branch, and pushes. It never turns a successful command into a failure. If the
remote cannot be reached, or a rebase conflicts, the changes stay committed in
the clone. The problem is reported on stderr (or in Claude's context, from the
prompt and edit hooks), and the next reconcile tries again; a rebase that fails or
times out is aborted, never left half done. Files in the project that the
repository's `.gitignore` excludes are not forced in, but a reconcile names them
rather than reporting success. git runs with prompts disabled and
a timeout, so credentials must work without being typed in, through an SSH
agent or a credential helper. Only the `file`, `git`, `http`, `https` and `ssh`
transports are allowed (`GIT_ALLOW_PROTOCOL`, which a git config setting cannot
widen); a `<helper>::<address>` URL, which would run a `git-remote-<helper>`
program, is refused. A whole commit is held to 25 seconds and a whole reconcile to
50, so neither outlasts the hook running it. A clone whose `pushurl` points
anywhere but `SZSDLC_BACKEND_GIT_URL` is refused, and its `.git/config`, which
holds the URL, is readable by its owner only.

### Adding a backend

A backend (sftp, http, ...) is a class implementing the `Backend` protocol in
[`backend/__init__.py`](../src/szsdlc/backend/__init__.py): its `type` and
`keys`, `from_settings`, `materialize`, `has_local_changes`, `commit` and `reconcile`, plus
`state`/`restore` so a store found on disk can be recognised again. Register it
in `BACKENDS`. Nothing outside that package knows which backend is in use.
[`dir.py`](../src/szsdlc/backend/dir.py) is the smallest example.

### Resolving an interpreter

The launchers in [`bin/`](../bin) are all a hook invokes, and have to find a
Python without asking anyone anything. In order:

1. an installed `szsdlc` already on `PATH` — a developer's own venv wins;
2. a venv the launcher built earlier, reinstalled over when the release moved;
3. otherwise build that venv, with the first interpreter that works of: an
   active `VIRTUAL_ENV`; a `.venv` beside the project or in any parent; every
   `python3.N` on `PATH`, newest first (on Windows, everything `py --list`
   reports); `python3`, `python`, `py`; and an interpreter `uv` manages;
4. failing that, run from the checkout with whichever of those already imports
   the three dependencies.

Candidates are probed by running them: on Windows `python` is frequently a
Store stub, which is on `PATH` and cannot run anything, and a `python3` that
is 3.9 on a box also shipping 3.12 is the common case rather than the exotic
one. Step 3 is a loop because a compatible interpreter can still fail to build
a venv where `ensurepip` is packaged separately.

This is in the launcher rather than the package because the interpreter is
already chosen by the time any szsdlc module is imported, and a package cannot
portably re-exec itself into another one — the same reason pip bakes a shebang
at install time, `uv tool` builds one venv per tool, and `py` is a separate
binary. The package's only say is `requires-python`, which the test suite
holds the launchers to.

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
