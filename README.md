# szsdlc

A project-agnostic agentic SDLC framework, distributed as a Claude Code plugin
plus a CLI. Every fact about work is stated once, in a machine-readable record;
every index, board, roadmap and back-link is generated from it. The
deterministic operations — what is next, what changed, is this consistent — are
script calls with fixed token cost rather than model reasoning over prose.

## Install

As a Claude Code plugin — this brings the hooks and the ten skills:

```bash
/plugin install szsdlc@szccpmp
```

Or as a plain CLI — installed under a namespaced name, run as `szsdlc`:

```bash
pip install zhaostephen-szsdlc
```

Then, in any project:

```bash
szsdlc init
```

To keep a project's files out of the repository, in another directory or in a
git remote that is synced automatically, see
[backends](docs/reference.md#backends-where-a-project-is-kept).

Python 3.12 or later. The plugin's launchers resolve an interpreter on their
own — an active virtualenv, a project `.venv`, any `python3.N` on `PATH`, or
one `uv` manages — so no virtualenv setup is needed for the hooks to work. See
[resolving an interpreter](docs/reference.md#resolving-an-interpreter).

## Documentation

- [`docs/reference.md`](docs/reference.md) — model, commands, configuration, hooks, development
- [`AGENTS.md`](AGENTS.md) — the agent-facing contract, six rules with reasons

szsdlc tracks its own SDLC in **szsdlc-szsdlc**, using the `git` backend. The
research that chose this design, the measured token costs, the architectural
decisions and the delivered implementation plan live there as entities — not as
prose in this repo, because a plan kept as prose beside the code it describes
is the exact drift this framework exists to remove. Run `szsdlc backend` to see
its sdlc backend and where its project sdlc root is.

## License

MIT — see [`LICENSE`](LICENSE).
