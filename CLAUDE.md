# CLAUDE.md

Guidance for Claude Code working in this repository.

> **Status:** the rewrite specified in `openspec/changes/rewrite-architecture/` is in progress. The pre-rewrite reference implementation is preserved at the `pre-rewrite` git tag; it has been removed from `main`. The architecture, contracts, and flows below reflect the rewrite, not the old code.

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
  kb/                   Kuzu (graph) + LanceDB (vector) hybrid retrieval
  build-pipeline/       lint, allowed-crate enforcement, cargo build
  py-bindings/          PyO3 module exposing trusted crates as `strategy_gpt._native`
python/strategy_gpt/    Orchestrator (LangGraph workflows, optimizer, tester, CLI)
kb/                     Curated source list and ingestion scripts
cache/                  Year-segmented content-addressed parquet (gitignored)
ledger/                 SQLite ledger + parquet sidecars (gitignored)
openspec/               Change proposals and capability specs
```

## Domain vocabulary

- **Strategy** — Rust crate implementing the sealed `engine_rt::Strategy` trait. Authored by the LLM through the build pipeline.
- **Parameters** — typed knobs the strategy exposes; mutable without recompilation.
- **Metrics** — Sharpe, Sortino, Profit Factor, Win Ratio, Max Drawdown, Annualized Return, trade-length stats.
- **Objective spec** — declarative, per-strategy: primary metric, secondary metrics with weights or hard constraints, tradeoff mode (`lexicographic`, `weighted_sum`, `pareto`), walk-forward configuration. Consumed by Evaluator and Optimizer uniformly.
- **Hypothesis** — named, human-readable claim that a specific change will move a target metric, with a falsification criterion.
- **Bar** — OHLCV bar with UTC timestamp; atomic input to strategies.
- **BatchSpec / RunSpec** — engine input. One strategy artifact, one dataset, many runs (parameters × modes × slices × seeds).
- **Modes** — `Plain`, `MonteCarlo { n, block_size }`, `Slippage { bps_grid }`, `RegimeFilter { ranges }`, `Sensitivity`.
- **Decision log** — ledger record of accepted/rejected hypotheses with rationale; reloaded as context on subsequent loop runs.

## Module roles (durable contract)

- **Data Gateway** — multi-provider fetch with year-segmented content-addressed cache, internal-only consolidation policy, divergence warnings to the ledger.
- **Backtest Engine** — batched, deterministic, abort-on-failure, native stress and sensitivity modes, enriched output schema (trades, signals incl. `suppressed_by`, equity, exec_log, regimes).
- **Strategy Runtime (`engine-rt`)** — sealed `Strategy` trait, `Context` capability handle, `RunnerVersion`, semver, no backwards compatibility.
- **Build Pipeline** — lint, allowed-crate whitelist (no version pinning), `cargo build` with sccache, content-addressed artifact cache.
- **Hypothesis Loop** — LangGraph workflow (`diagnose`, `kb_query`, `generate`, `critique`, `rank`, `select`) with internal iteration and persisted decision log.
- **Tester** — translate hypothesis to artifact, run lint + smoke + full batch, report verdict against falsification criterion.
- **Parameter Optimizer** — in-house grid/random/Bayesian over walk-forward folds, multi-metric objectives, LLM-generated rationale.
- **Knowledge Base** — Kuzu + LanceDB hybrid, curated ingestion, citation-friendly retrieval.
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

# Rust toolchain pin: 1.82.0 (rust-toolchain.toml). sccache is opt-in via
# RUSTC_WRAPPER=sccache (.cargo/config.toml documents the recommended setup).
```

No CI yet. No global linter pass yet. Both land in phase 13.

## Environment

- `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY` — for hypothesis loop reasoning calls.
- `RUSTC_WRAPPER=sccache` — recommended for build speed.

## Working in this repo

- Specs live under `openspec/changes/rewrite-architecture/specs/<capability>/spec.md`. Code must satisfy the named requirements; scenarios are testable.
- Strategy code is the *only* place LLM output runs as native code. All other Rust is human-authored and trusted.
- The `Strategy` trait is sealed. Strategies are generated by the build pipeline, not hand-written outside it.
- Old `pre-rewrite` artifacts at the tag of the same name; do not import or revive them.
