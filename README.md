# strategy-gpt

LLM-driven research loop for **creating and testing** quantitative trading strategies. Polyglot: a Rust core (engine, data gateway, ledger, knowledge base, build pipeline) wrapped by a Python orchestrator (LangGraph hypothesis loop, optimizer, tester).

> This is a **research platform, not a trading platform.** The engine simulates fills against historical bars purely to evaluate hypotheses. There is no live trading, no broker integration, no real-time position management.

The product is the *loop*: `hypothesis → code → backtest → verdict → next hypothesis`. The reference example bundled in this repo is a VXX volatility-range short strategy with treasury hedging.

## Architecture

```
                    Python orchestrator (LangGraph)
              ┌──────────────────────────────────────┐
              │  Hypothesis Loop · Tester · Optimizer │
              └──────────────┬───────────────────────┘
                             │ PyO3 (in-process, trusted)
                             ▼
              ┌──────────────────────────────────────┐
              │  Rust core                            │
              │  data-gateway · ledger · kb · build   │
              │  engine (control plane)               │
              └──────────────┬───────────────────────┘
                             │ subprocess + Arrow IPC
                             ▼
              ┌──────────────────────────────────────┐
              │  Engine workers (1..N)                │
              │  load compiled strategy artifact      │
              │  drive Strategy lifecycle over bars   │
              └──────────────────────────────────────┘
```

Full architecture write-up: [`docs/explanation/architecture.md`](docs/explanation/architecture.md).

## Quickstart

```bash
# 1. Rust workspace
cd crates && cargo check --workspace

# 2. Python orchestrator + native PyO3 extension
cd ../python && pip install -e '.[dev]'
pip install maturin
maturin develop -m ../crates/py-bindings/Cargo.toml

# 3. Build the reference strategy + worker
cd ../crates && cargo build -p vxx-strategy -p example-strategy --bin engine-worker

# 4. Pre-commit hooks (matches the lint suite CI runs)
cd .. && pre-commit install
```

`make lint` and `make test` (from the repo root) are the canonical entry points after setup.

## Documentation

Operator and engineer docs live under [`docs/`](docs/). The site is built with MkDocs Material and versioned by release branches via `mike`.

| Where | What |
|---|---|
| [`docs/index.md`](docs/index.md) | Landing — audience picker + intent-based navigation. |
| [`docs/for-quants/`](docs/for-quants/) | Reading path for strategy authors and researchers. |
| [`docs/for-engineers/`](docs/for-engineers/) | Reading path for platform engineers. |
| [`docs/tutorials/`](docs/tutorials/) | Learn-by-doing walkthroughs. |
| [`docs/how-to/`](docs/how-to/) | Task recipes — CLI cookbook, PBO rejection triage, more. |
| [`docs/reference/`](docs/reference/) | Schemas, knobs, CLI surface. |
| [`docs/explanation/`](docs/explanation/) | Vocabulary, architecture, methodology, bibliography. |
| [`docs/decisions/`](docs/decisions/) | Architecture Decision Records. |
| [`openspec/specs/`](openspec/specs/) | Normative capability contracts. |

Build the site locally:

```bash
pip install -r requirements-docs.txt
make docs-serve     # mkdocs serve at http://127.0.0.1:8000
make docs-build     # mkdocs build --strict
```

## Lint, test, CI

Single source of truth: `make lint` (same suite locally and in CI).

```bash
make lint     # rustfmt --check + clippy + ruff check + ruff format --check + mypy --strict + mkdocs build --strict
make fmt      # write fixes (rustfmt + ruff format)
make test     # cargo test --workspace + pytest
```

Stance:

- **Rust** — tool defaults. `cargo fmt --all -- --check` and `cargo clippy --workspace --all-targets -- -D warnings`. No `.rustfmt.toml` or `clippy.toml`.
- **Python** — strict. Ruff (`E,F,W,I,B,UP,SIM,RUF,S,N,PT,ANN,C4,ERA,PL`) + `ruff format --check` + `mypy --strict` (scope: `python/strategy_gpt/`).
- **Docs** — `mkdocs build --strict` fails on broken links.

Tool versions pinned in `.pre-commit-config.yaml` and `requirements-docs.txt`. CI runs `make lint` + `make test` plus a smoke-fixture byte-identity check (`.github/workflows/ci.yml`).

## OpenSpec

Capability changes go through OpenSpec — see [`openspec/`](openspec/). Specs are the *normative* contract; `docs/` documents how to operate the system.

## License

See [`LICENSE`](LICENSE).
