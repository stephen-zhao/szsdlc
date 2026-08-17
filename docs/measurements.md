# Measurements

The efficiency claim in this project's design is that a session should learn
the state of the work from a **counter**, not from a listing, and that the
cost of doing so should not grow with the project. This file is the evidence.

Everything here is measured, not estimated. Token counts come from a BPE
tokenizer (`cl100k_base`) rather than a characters-divided-by-four rule of
thumb. It is not Claude's tokenizer, so read the absolute numbers as a close
proxy; the *ratios* between commands are what the claim rests on, and those
survive the difference.

Measured on WSL Ubuntu-24.04, Python 3.12.3, warm page cache, best of five.

## Output cost

Tokens of stdout, on a project of the given size. "20 entities" is a small
real project; "200" is a large one.

| command | 20 entities | 200 entities | grows with project? |
|---|---:|---:|---|
| `context` | **33** | **33** | no |
| `next` | 216 | 216 | no — capped at 10 |
| `inbox` | 110 | 456 | capped at 20 rows |
| `inbox --limit 0` | 110 | 1100 | yes |
| `list --unscheduled` | 100 | 416 | capped at 20 rows |
| `list --unscheduled --limit 0` | 100 | 1000 | yes |
| `list --limit 0` | 724 | 7024 | yes |

## The counters-versus-listing comparison

This is the number the design exists for. `szsdlc context` reports how much
work is unscheduled as a **scalar**, inside a whole payload costing 33 tokens.
The listing that scalar replaces is `list --unscheduled --limit 0`:

| project size | `context` (whole payload) | the listing it replaces | ratio |
|---|---:|---:|---:|
| 20 entities | 33 | 100 | 3× |
| 200 entities | 33 | 1000 | **30×** |

The ratio is the interesting part: the counter is flat and the listing is
linear, so the saving grows with exactly the thing that makes a project hard
to hold in context. At 200 entities a session that opens by reading the whole
unscheduled list has spent a thousand tokens to learn a number.

The default-bounded forms (`inbox`, `list --unscheduled`) are the middle
ground — 20 rows and a visible truncation note — and they exist so that
"show me" costs a bounded amount too.

## Load time

Every hook invocation pays a full load: parse the config, read every entity,
build the relation graph. There is no index and no cache.

| project size | graph load | whole `context` command |
|---|---:|---:|
| 200 entities | 0.13 s | 0.38 s |
| 2000 entities | 1.04 s | 1.76 s |

The `context` column includes interpreter startup and import, which is what a
hook actually experiences.

### The decision not to cache

**No index cache is added.** At 200 entities — a large real project — a hook
costs 0.38 s, which nobody notices. A cache keyed on mtime would buy back most
of that and introduce exactly the failure this framework exists to eliminate:
state that disagrees with the files while claiming not to.

At 2000 entities the `PostToolUse` sync costs 1.76 s per file write, which is
noticeable. Revisit if a real project reaches that size, and prefer narrowing
what a hook loads over caching what it loaded last time.

### A related finding, recorded rather than fixed

Building a project **through the CLI** is O(n²): every mutation reloads the
whole store, so 2000 entities created one command at a time is quadratic and
impractically slow. This is correct for the actual usage — a human or an agent
issues one command, thinks, and issues another — and the fixture for the load
benchmark above is therefore written straight to disk. It matters only if bulk
import is ever added, at which point the answer is a bulk path, not a cache.
