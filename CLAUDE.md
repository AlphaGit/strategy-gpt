# CLAUDE.md

Guidance for Claude Code working in this repository.

## Purpose

Strategy-GPT is an **LLM-driven research loop for creating and testing quantitative trading strategies**. Given a strategy plus its parameters and recent backtest performance, the loop diagnoses weaknesses, generates testable hypotheses informed by a curated knowledge base, codes and backtests them, and persists every accepted/rejected decision so the system improves over time.

**This is a research platform, not a trading platform.** The product is the *loop*: hypothesis → code → backtest → verdict → next hypothesis. Out of scope: live trading, order routing, real-time position management, broker integrations beyond historical data fetch. Inside a backtest the engine simulates fills purely so strategies can be evaluated; positions are accounting state for the backtest, not a live book.

The reference example is a VXX volatility-range strategy with treasury hedging; the platform is strategy-agnostic.

## Architecture (one-screen)

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

Trust boundaries:
- **PyO3 boundary** — only trusted Rust crates we own. No LLM-emitted code in-process.
- **Worker boundary** — LLM-compiled strategy lives here. Process isolation only (no sandboxing); a worker crash never takes down the orchestrator.

## Repo layout

```
crates/                 Rust workspace
  engine-rt/            Strategy trait, Context, RunnerVersion
  engine/               BatchSpec, coordinator, worker, modes
  data-gateway/         providers, cache, normalizer, consolidator
  ledger/               SQLite append-only + parquet sidecars
  kb/                   Hybrid retrieval (graph + vector) over a SQLite-backed
                        store. The retrieval contract is the load-bearing
                        interface, not the storage choice.
  build-pipeline/       lint, allowed-crate enforcement, cargo build
  py-bindings/          PyO3 module exposing trusted crates as `strategy_gpt._native`
  vxx-strategy/         Reference VXX volatility-range smoke strategy cdylib
  example-strategy/     No-op fixture used by plugin-loader tests
python/strategy_gpt/    Orchestrator (LangGraph workflows, optimizer, tester,
                        author, CLI, smoke run). `author.py` drives the
                        interactive intent dialog and the emit/build/smoke
                        loop for the `strategy-gpt author` command.
                        `hypothesize_wiring.py` builds `HypothesizeDeps`
                        end-to-end (crate paths, KB lazy-build, per-stage
                        reasoning router, evaluate-fold factory, baseline
                        resolution) for the `strategy-gpt hypothesize`
                        command.
kb/                     Curated source list, starter corpus, recorded fixtures
cache/                  Year-segmented content-addressed parquet (gitignored)
ledger/                 SQLite ledger + parquet sidecars (gitignored)
openspec/               Change proposals and capability specs
```

`crates/Cargo.toml` declares `members = ["*"]` so any crate dropped into
`crates/` (including author-emitted strategy crates) is auto-included in
the workspace. Non-crate subdirs (e.g. `experiment-spec/`, `target/`,
`.pytest_cache/`) are listed under `[workspace] exclude = [...]`.

## Domain vocabulary

See [docs/explanation/domain-vocabulary.md](docs/explanation/domain-vocabulary.md).

## Module roles (durable contract)

- **Data Gateway** — multi-provider fetch with year-segmented content-addressed cache, internal-only consolidation policy, divergence warnings to the ledger.
- **Backtest Engine** — batched, deterministic, abort-on-failure, native stress and sensitivity modes, enriched output schema (trades, signals incl. `suppressed_by`, equity, exec_log, regimes).
- **Strategy Runtime (`engine-rt`)** — sealed `Strategy` trait, `Context` capability handle, `RunnerVersion`, semver, no backwards compatibility.
- **Build Pipeline** — lint, allowed-crate whitelist (no version pinning), `cargo build` with sccache, content-addressed artifact cache.
- **Author** — interactive LLM-driven creation (and editing) of strategy crates. `run_intent_dialog` elicits a structured `AuthorIntent` from the operator, `author_strategy` runs the emit/build/smoke repair loop, and `run_author_session` wraps both with repair-budget recovery. The authoritative dialog state lives in `crates/<name>-strategy/.author/decisions.jsonl` (a typed event log) — the LLM's chat history is non-load-bearing, so compaction never loses locked-in decisions. On-disk crate (with `intent.toml` + `smoke.toml`) is the durable artifact. No ledger row, no verdict — success means the crate compiles and smoke passes.
- **Hypothesis Loop** — LangGraph workflow (`diagnose`, `kb_query`, `generate`, `critique`, `rank`, `select`) with internal iteration and persisted decision log. Driven from `strategy-gpt hypothesize <name>`; the CLI builds `HypothesizeDeps` via `hypothesize_wiring.py` (crate paths, KB client, stage reasoning client, build pipeline, **per-candidate evaluator factory** — mini-optimize runs the candidate's freshly-built library, not the baseline's). Each stage emission goes through a bounded repair loop that feeds the LLM the previous emission + validator error so retries patch in place. Mechanical code-emission failures (`reject_build` / `reject_lint` / `reject_format` / `reject_deps` / `exhausted_repair_budget`) persist as `deferred` (NOT `rejected`) and do NOT bias future ideation — the hypothesis is preserved. Per-stage + per-attempt + per-trial progress streams to stderr; `--quiet` suppresses.
- **Tester** — translate hypothesis to artifact, run lint + smoke + full batch, report verdict against falsification criterion.
- **Parameter Optimizer** — in-house per-fold search (grid, random, Sobol, Bayesian/TPE, recursive grid, LHS+Hooke-Jeeves, successive halving, CMA-ES, differential evolution) over the experiment-spec fold scheme, multi-metric objectives, overfitting-aware selection layer, LLM-generated rationale.
- **Knowledge Base** — hybrid graph + vector retrieval over a SQLite-backed store, curated ingestion, citation-friendly retrieval.
- **Experiment Ledger** — SQLite append-only + parquet sidecars; sufficient (with cache) to byte-identical reproduce any run.

## Reproducibility

Every run pins: strategy artifact hash, dataset manifest hash, parameters, modes, seed, runner version. Identical inputs produce byte-identical `BacktestResult`. Replays load from the ledger.

## Build / develop

```bash
# Rust workspace
cd crates && cargo check --workspace

# Python orchestrator (run once, then `maturin develop` to build native bindings)
cd python && pip install -e '.[dev]'
maturin develop -m ../crates/py-bindings/Cargo.toml

# Docs (mkdocs-material + mike). Optional, but `make lint` invokes
# `mkdocs build --strict` so install if you touch docs/.
pip install -r requirements-docs.txt
make docs-serve   # http://127.0.0.1:8000

# Rust toolchain pin: 1.82.0 (rust-toolchain.toml). sccache is opt-in via
# RUSTC_WRAPPER=sccache (.cargo/config.toml documents the recommended setup).
```

## Lint and pre-commit

Single source of truth: `make lint`. Same suite runs locally and in CI.

```bash
# One-time setup
pre-commit install

# Manual full-tree run
make lint        # rustfmt --check + clippy + ruff check + ruff format --check + mypy --strict
make fmt         # write fixes (rustfmt + ruff format)
```

Stance:
- **Rust**: tool defaults. No `.rustfmt.toml` or `clippy.toml`. `cargo fmt --all -- --check` and `cargo clippy --workspace --all-targets -- -D warnings`.
- **Python**: strict. Ruff with a wide rule set (`E,F,W,I,B,UP,SIM,RUF,S,N,PT,ANN,C4,ERA,PL`) plus `ruff format --check` plus `mypy --strict`. Mypy strict scope is `python/strategy_gpt/`; `kb/` and tests are excluded explicitly.
- Tool versions are pinned in `.pre-commit-config.yaml`. Pre-commit hooks scope to staged files; `make lint` covers the whole tree.
- CI (`.github/workflows/ci.yml`) runs `make lint` + `make test` plus a smoke-fixture byte-identity check.

## Environment

- `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY` — for hypothesis loop reasoning calls.
- `RUSTC_WRAPPER=sccache` — recommended for build speed.

## Working in this repo

- Specs live under `openspec/specs/<capability>/spec.md`. Code must satisfy the named requirements; scenarios are testable.
- Strategy code is the *only* place LLM output runs as native code. All other Rust is human-authored and trusted.
- The `Strategy` trait is sealed. Strategies are generated by the build pipeline, not hand-written outside it.
