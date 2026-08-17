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

## Documentation

- [`docs/reference.md`](docs/reference.md) — model, commands, configuration, hooks, development
- [`AGENTS.md`](AGENTS.md) — the agent-facing contract, six rules with reasons
- [`docs/measurements.md`](docs/measurements.md) — measured token cost and load time
- [`docs/research.md`](docs/research.md) — the evidence and the options weighed
- [`docs/plan.md`](docs/plan.md) — the implementation plan, with every decision recorded against the task that forced it

## License

MIT — see [`LICENSE`](LICENSE).
