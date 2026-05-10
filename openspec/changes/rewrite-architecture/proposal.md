## Why

The current strategy-gpt codebase is a single-file reference implementation that conflates LLM orchestration with strategy execution, runs LLM-generated code via in-process `exec`, has no experiment ledger, no real backtest engine, and a broken outer mutation loop. It cannot scale beyond toy demonstrations and is unsafe to run unattended. We are rewriting it as a polyglot, modular **research system** whose product is the *loop* — a durable platform that creates and tests arbitrary quantitative strategies, reasons about backtest results with the help of a curated knowledge base, and proposes, tests, and accepts changes empirically. **This is not a trading platform.** Live trading, order routing, and real-time position management are out of scope; the engine simulates fills only as a means to evaluate hypotheses.

## What Changes

- **BREAKING**: Replace the entire reference implementation. New architecture is Python orchestration (LangGraph) + Rust engine, communicating via PyO3 in-process for trusted code paths and subprocess + Arrow IPC for strategy execution.
- Introduce a **Backtest Engine** in Rust that accepts batches of runs (one strategy, many params/modes/slices) and supports stress testing (Monte Carlo block bootstrap, slippage perturbations, regime filters) and parametric sensitivity sweeps natively.
- Strategies are written as **native Rust** code conforming to a sealed `Strategy` trait. Each strategy carries a runner-version number; the runner follows semantic versioning with no backward-compatibility maintenance — old strategies are detected and migrated.
- Introduce a **Data Gateway** in Rust with a content-addressed local cache, multi-provider fetch (yfinance, Polygon, IBKR, Alpaca, CSV/parquet at v1+), normalization (UTC, calendar, adjustments), and a consolidator with internally-configured policies that surface divergence warnings to the ledger.
- Introduce a **Hypothesis Module** as a LangGraph workflow that diagnoses backtest results, queries the knowledge base, generates and self-critiques candidate hypotheses, and persists a decision log of accepted and rejected options with rationale. Loops until candidates pass critique or budget exhausts.
- Introduce a **Tester Module** that translates a hypothesis into a concrete strategy diff or new strategy, smoke-tests the Rust source compiles and parses, then delegates to the engine.
- Introduce a **Parameter Optimizer** (custom, in-house) supporting grid, random, and Bayesian search over walk-forward folds, emitting both an optimized parameter set and a natural-language rationale grounded in optimizer output and the knowledge base.
- Introduce a **Knowledge Base** built on Kuzu (graph) + LanceDB (vector), populated by a curated ingestion pipeline over investment books, papers, and other resources.
- Introduce an **Experiment Ledger** (SQLite) that records every hypothesis, strategy version, dataset manifest pin, run configuration, verdict, and divergence warning.
- Strategies execute in their own process (separate from the orchestrator) but **without sandboxing** — process isolation only. Trust assumption: strategies are produced by our own pipeline.
- **Multi-metric objectives** are parametric per strategy: declarative spec covering primary metric, secondary metrics with weights/constraints, and tradeoff mode (lexicographic, weighted_sum, pareto).
- Allowed-crate **whitelist** for LLM-emitted strategies; versions are not pinned (latest within whitelist).
- Cache is **segmented by calendar year** for range-aware reuse across walk-forward slices.

## Capabilities

### New Capabilities

- `data-gateway`: Multi-provider market data fetching, content-addressed local cache, normalization, and multi-source consolidation with divergence warnings.
- `backtest-engine`: Rust-native backtest engine that runs batches of strategy executions, supports stress and sensitivity modes, and emits enriched result frames (trades, signals fired and suppressed, decision log, equity curve, regimes).
- `strategy-runtime`: The `Strategy` trait, `Context` capability handle, build pipeline (allowed-crate whitelist, sccache), versioning rules, and worker process model.
- `hypothesis-loop`: LangGraph-orchestrated reasoning loop that diagnoses results, queries the knowledge base, generates and critiques hypotheses, and persists an accept/reject decision log.
- `tester`: Translates a hypothesis into Rust strategy code or a typed parameter diff, validates compilation, and delegates batch backtests to the engine.
- `param-optimizer`: In-house optimizer over walk-forward folds, supporting grid/random/Bayesian methods, multi-metric objectives, and rationale generation.
- `knowledge-base`: Kuzu + LanceDB hybrid store for curated financial knowledge with ingestion, retrieval, and graph-aware reasoning.
- `experiment-ledger`: SQLite-backed append-only record of all runs, hypotheses, decisions, dataset manifest pins, and divergence warnings.
- `objectives`: Declarative per-strategy multi-metric objective specification consumed by the evaluator and optimizer.

### Modified Capabilities

None — greenfield rewrite, no existing specs.

## Impact

- **Code**: Whole repo replaced. New layout: `crates/` (Rust workspace), `python/` (orchestrator + LangGraph workflows), `kb/` (ingestion pipeline), `cache/`, `ledger/`.
- **APIs**: New PyO3 bindings (data gateway, engine control, ledger, KB client). New CLI for engine binary. New Rust trait surface (`Strategy`, `Context`).
- **Dependencies (Rust)**: `polars`, `ndarray`, `chrono`, `serde`, `arrow`, `pyo3`, `kuzu`, `lancedb`. **Dependencies (Python)**: `langgraph`, `pydantic`, `duckdb`/`sqlite3`, `optuna` (reference for in-house optimizer design), provider SDKs as needed.
- **Build/CI**: Rust toolchain pinned, `sccache`, allowed-crate registry mirror, cross-platform builds (macOS, Linux). Python `poetry`/`uv`.
- **Data**: Existing `cache/` feather files are not migrated; cache is rebuilt under new content-addressed layout.
- **Migration**: No backward compatibility with current code. Reference strategy (VXX volatility ranges + treasury hedge) is reimplemented under the new contracts as a smoke-test strategy.
- **Out of scope (this change)**: Live trading, paper trading, real-time order routing, broker integrations beyond historical data fetch, real-time position management, P&L dashboards, web UI, distributed/multi-node execution, end-to-end outer mutation loop wiring (Hypothesis verdict → adopted strategy mutation is in scope; production-grade autonomous run-until-converged is not).
