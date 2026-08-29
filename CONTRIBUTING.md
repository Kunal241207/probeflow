# Contributing to probeflow

probeflow is a local CLI that runs Git-diffable `.http` files as an enforceable
test suite. Contributions are welcome — please read the non-goals first, since
the fastest way to have a PR declined is to propose something on that list.

## Non-goals

These are deliberate, permanent scope limits, not missing features or TODOs.
probeflow stays a single-purpose CLI that does one thing well:

- **No TUI.** Output is plain text that reads well in a terminal and in CI logs.
- **No mock servers.** probeflow sends real requests to real endpoints. Use a
  dedicated mocking tool if you need one.
- **No GUI.** The `.http` file is the interface.
- **No cloud sync.** No accounts, no hosted service, no team workspace. Your
  request files live in your repo and are versioned by Git.
- **No OpenAPI import.** Generating `.http` files from a schema is a separate
  concern and belongs in a separate tool.
- **No plugin marketplace.** No plugin loader, registry, or extension API.
- **No script execution without `--allow-scripts`.** `### @before` / `### @after`
  hooks are parsed and validated but never executed implicitly. Running code
  found in a data file must always be an explicit, opt-in decision.

## Development setup

```bash
pip install -e ".[dev]"
```

## Before opening a PR

```bash
python -m pytest          # full suite; coverage gate is 85%
python -m ruff check .
python -m ruff format --check .
```

All three must pass. CI runs the suite on Python 3.11, 3.12, and 3.13.

If you change the `.http` grammar, update [`docs/spec.md`](docs/spec.md) in the
same PR — the spec is the contract, not the implementation.

## Compatibility

probeflow is published on PyPI, so people have it installed. Changes that alter
existing behaviour — CLI flags, exit codes, output that scripts might parse, or
how a valid `.http` file is interpreted — need a clear justification and a note
in the PR description. Prefer extending over changing.

## Reporting issues

Bugs and feature requests: open a GitHub issue with a minimal `.http` file that
reproduces the problem, plus expected and actual output.

Security vulnerabilities: do not open a public issue. Follow
[SECURITY.md](SECURITY.md).
