## 1. Repo Setup

- [x] 1.1 Tag current `main` as `pre-rewrite` so the reference implementation remains accessible
- [x] 1.2 Remove the existing reference implementation from `main` (keep `cache/` ignore rules and project metadata)
- [x] 1.3 Create the polyglot layout: `crates/` (Rust workspace root with `Cargo.toml`), `python/` (orchestrator package), `kb/` (ingestion scripts), `cache/` (gitignored), `ledger/` (gitignored)
- [x] 1.4 Add Rust toolchain pin (`rust-toolchain.toml`) and `sccache` configuration in `crates/`
- [x] 1.5 Initialize Python project under `python/` with `pyproject.toml`, dependency groups for `langgraph`, `pyo3` consumer bindings, `duckdb`/`sqlite3`, anthropic/openai SDKs
- [x] 1.6 Wire `pyo3-build-config`/`maturin` so the Python package can import the Rust crates as native extensions
- [x] 1.7 Update `CLAUDE.md` with the new layout and remove outdated "reference implementation" notes once phase 2 lands

## 2. Strategy Runtime — `crates/engine-rt`

- [x] 2.1 Define the sealed `Strategy` trait with `metadata`, `on_init`, `on_bar`, `on_fill`, `on_end` lifecycle methods
- [x] 2.2 Define the `Context` capability struct exposing `submit_order`, `cancel_order`, `get_position`, `log_signal`, `log_decision`, `read_indicator`, and engine-managed state get/set
- [x] 2.3 Implement the backtest fill simulator and position-accounting primitives that `Context` delegates to (next-bar-open and current-bar-close fill models, fee/slippage hooks, per-symbol position aggregation). No live order book, no cancellation pathway.
- [x] 2.4 Define the engine-provided indicator registry and a baseline indicator set (SMA, EMA, RSI, ATR, realized vol)
- [x] 2.5 Add the `RunnerVersion` constant and embed it into every compiled artifact's metadata
- [x] 2.6 Write unit tests for `Context` order routing, position math, and signal logging

## 3. Build Pipeline for LLM-Emitted Strategies

- [x] 3.1 Implement the source linter that rejects `unsafe`, banned APIs (process/syscalls/network/filesystem), and non-whitelisted crate references — `syn`-based AST visitor rejects `unsafe` blocks, `unsafe fn`, `extern "C"`, and `extern crate`; manifest linter rejects non-whitelisted dependencies
- [x] 3.2 Define the allowed-crate whitelist as a versioned manifest in `crates/build-pipeline/whitelist.toml` (initial set: `polars`, `ndarray`, `chrono`, `serde`, `engine-rt`)
- [ ] 3.3 Stand up a local cargo registry mirror (or vendored crate cache) that serves only whitelisted crates — deferred until the LLM strategy generator lands; until then the linter is the operative whitelist enforcement
- [ ] 3.4 Implement the build driver: receive Rust source, lay out a Cargo project, run `cargo build` with `sccache`, return artifact path and metadata — `BuildDriver` orchestration done (lint → cache → cargo via injected `Cargo` trait → store); production `SystemCargo` that shells out to cargo deferred until 3.3 lands
- [x] 3.5 Implement content-addressed artifact cache keyed by `hash(source)` with reuse on repeat input — `blake3(source + canonical_manifest + runner_version)` keying, on-disk metadata, dependency-order-stable
- [x] 3.6 Implement runner-version migration: detect old artifacts on load, regenerate source via the LLM under the new ABI, rebuild — decision logic implemented (`migration_decision`); LLM regenerate-source step lands with the orchestrator pipeline
- [x] 3.7 Cover the build pipeline with integration tests: happy path, rejected crate, compile failure, artifact reuse — 28 unit + integration-style tests across linter, whitelist, artifact_cache, driver, migration; `Cargo` trait stubbed with `StubCargo` so real `cargo build` is not required for CI

## 4. Backtest Engine — `crates/engine`

- [x] 4.1 Define `BatchSpec`, `RunSpec`, `Mode` enum (`Plain`, `MonteCarlo`, `Slippage`, `RegimeFilter`, `Sensitivity`), and the `BacktestResult` schema — all variants defined; only `Plain` wired into the executor
- [ ] 4.2 Implement the worker process binary that loads a strategy artifact, drives the lifecycle methods over a bar stream, and streams `BacktestResult` over Arrow IPC on stdout — pending; in-process executor exists and shares the loop with the eventual worker
- [ ] 4.3 Implement the coordinator: spawn N worker processes, dispatch runs, enforce per-run time and memory caps via OS primitives, kill on exceedance — pending; depends on 4.2
- [x] 4.4 Implement abort-on-failure batch semantics: a single run failure cancels remaining runs and reports a structured failure — `run_batch` aborts on first run failure with `BatchError::Run { index, source }`
- [x] 4.5 Implement deterministic execution: seeded RNG, stable bar iteration, identical output for identical inputs — verified by integration test `determinism_identical_inputs_produce_identical_output`
- [x] 4.6 Implement enriched output capture: trades, signals (including `suppressed_by`), equity, exec_log — all four channels populated by `run_one`
- [x] 4.7 Implement `MonteCarlo` mode with block bootstrap over input bars — `modes::monte_carlo` resamples blocks with seeded RNG, re-stamps timestamps, aggregates metrics into one `StressScenario`
- [x] 4.8 Implement `Slippage` and `RegimeFilter` stress modes — `slippage_sweep` clones engine config per bps; `regime_filter` runs per range with filtered bars
- [x] 4.9 Implement parametric `Sensitivity` sweep mode with per-point sub-results — `sensitivity_sweep` overrides numeric param in `run.params`, dedups identical values
- [x] 4.10 Compute post-hoc regime annotations (volatility regime + trend regime) for `BacktestResult.regimes` — `regime::annotate_regimes` emits `low_vol`/`med_vol`/`high_vol` and `uptrend`/`downtrend`/`chop` runs
- [ ] 4.11 Expose the engine control plane to Python via PyO3 (`submit_batch`, `poll`, `cancel`)
- [ ] 4.12 Determinism golden-test: run a known strategy + dataset twice and assert byte-identical results — basic version exists in `end_to_end.rs`; richer dataset + checked-in fixture pending

## 5. Data Gateway — `crates/data-gateway`

- [ ] 5.1 Define the `Provider` trait and the normalized `Bar` type (UTC timestamps, exchange-local kept as auxiliary field)
- [ ] 5.2 Implement the `yfinance` provider
- [ ] 5.3 Implement the generic CSV/parquet provider for bring-your-own data files
- [ ] 5.4 Implement the year-segmented content-addressed cache: parquet blobs under `cache/`, manifest table in SQLite
- [ ] 5.5 Implement cache modes: `prefer-cache`, `validate`, `force-refresh`, `offline`
- [ ] 5.6 Implement the normalizer: timezone conversion, calendar alignment per session calendar, adjustment-policy tagging
- [ ] 5.7 Implement the consolidator with internal-only configuration: precedence order, close/volume tolerance, on-disagree behavior, missing-bar handling
- [ ] 5.8 Emit divergence warnings and route them to the experiment ledger
- [ ] 5.9 Issue manifests with every returned dataset that uniquely identify the cache blobs used
- [ ] 5.10 Expose the gateway to Python via PyO3 (`fetch`, `manifest_for`, `cache_stats`)
- [ ] 5.11 Tests: cache hit/miss, year-segment merging, multi-provider divergence, offline mode error

## 6. Experiment Ledger — `crates/ledger` + Python client

- [ ] 6.1 Design the SQLite schema: `runs`, `hypotheses`, `decisions`, `dataset_manifests`, `divergence_warnings`, `objectives`, `strategy_versions`
- [ ] 6.2 Enforce append-only semantics via triggers that reject UPDATE/DELETE on the protected tables
- [ ] 6.3 Implement parquet sidecar I/O for trades, signals, equity, and exec_log keyed by run id
- [ ] 6.4 Implement the recent-decisions query for the Hypothesis Loop's state initialization
- [ ] 6.5 Implement the run replay path: given a run id, reconstruct `BatchSpec` and dataset for byte-identical reproduction
- [ ] 6.6 PyO3 bindings: `record_run`, `record_hypothesis`, `record_decision`, `record_divergence`, `query_recent_decisions`, `replay_run`
- [ ] 6.7 Tests: append-only enforcement, sidecar round-trip, replay produces identical `BacktestResult`

## 7. Objectives Spec

- [ ] 7.1 Define the YAML/JSON schema for objective specs (primary, secondary, tradeoff, walk-forward, oos_min_score)
- [ ] 7.2 Implement spec validation (metric names valid against engine output, comparison operators, weight non-negativity, pareto requires ≥2 metrics)
- [ ] 7.3 Implement evaluator: given metrics + spec, return pass/fail and aggregated score under the chosen tradeoff mode
- [ ] 7.4 Tests: constraint violation, lexicographic tiebreak, weighted_sum scoring, pareto frontier extraction

## 8. Knowledge Base — `crates/kb` + ingestion

- [ ] 8.1 Define the Kuzu schema (node types: `Concept`, `Indicator`, `Regime`, `Model`, `Technique`, `Source`; relations: `IMPLEMENTS`, `CONTRADICTS`, `REQUIRES`, `GENERALIZES`, `CITES`, `EMPIRICAL_SUPPORT`, `FAILS_IN_REGIME`)
- [ ] 8.2 Provision the LanceDB collections for chunk embeddings with provenance metadata
- [ ] 8.3 Implement the curated source list format (TOML or JSON) with per-source ingestion config
- [ ] 8.4 Implement the ingestion pipeline: chunker, embedder, LLM entity/relation extractor, writer (Kuzu + LanceDB transactional pair)
- [ ] 8.5 Implement hybrid retrieval: vector top-k → graph neighborhood expansion → re-rank → unified result
- [ ] 8.6 Ensure every retrieval result carries source provenance for citation
- [ ] 8.7 Expose the KB client to Python via PyO3 (`retrieve`, `add_source`, `reingest`)
- [ ] 8.8 Ingest a small starter corpus (2–3 books or papers) as smoke-test content
- [ ] 8.9 Tests: retrieval over starter corpus, citation presence, offline operation

## 9. Hypothesis Loop — `python/hypothesis_loop`

- [ ] 9.1 Define the LangGraph state schema (`accepted`, `rejected`, `open`, `kb_cites`, `iteration`, `termination_reason`)
- [ ] 9.2 Implement node `diagnose` (analyze trade clusters, regime performance, signal misfires from `BacktestResult`)
- [ ] 9.3 Implement node `kb_query` (call KB hybrid retrieval and attach citations)
- [ ] 9.4 Implement node `generate` (reasoning model emits N candidate hypotheses with falsification criteria)
- [ ] 9.5 Implement node `critique` (reasoning model attacks each candidate; reject or annotate)
- [ ] 9.6 Implement node `rank` (score candidates by expected lift, evidence strength, complexity)
- [ ] 9.7 Implement node `select` (choose top-K and emit to Tester)
- [ ] 9.8 Implement the inner iteration loop with three termination conditions (sufficient candidates / budget exhausted / similarity saturation)
- [ ] 9.9 Persist accepted and rejected decisions to the experiment ledger with rationale and citations
- [ ] 9.10 Re-load recent decisions from the ledger on workflow start
- [ ] 9.11 Make reasoning model selection configurable (default: most capable available)
- [ ] 9.12 Tests with recorded fixtures: golden hypothesis generation against a fixed `BacktestResult`

## 10. Tester — `python/tester`

- [ ] 10.1 Implement hypothesis-to-artifact translation for parameter-only diffs (no recompile)
- [ ] 10.2 Implement hypothesis-to-artifact translation for logic changes (LLM-emitted Rust source through the build pipeline)
- [ ] 10.3 Run build + lint validation; on failure, record `rejected: build_failed` with diagnostics in the ledger
- [ ] 10.4 Run a smoke backtest on a small slice; on failure, record `rejected: smoke_failed` with cause
- [ ] 10.5 Construct the full `BatchSpec` (walk-forward folds + configured stress/sensitivity modes) and submit to the engine
- [ ] 10.6 Evaluate verdict against the hypothesis's falsification criterion and report back to the Hypothesis Loop
- [ ] 10.7 Tests: build-fail path, smoke-fail path, successful end-to-end with a fixture strategy

## 11. Parameter Optimizer — `python/optimizer`

- [ ] 11.1 Implement the optimizer driver that evaluates candidates by submitting batches to the engine across walk-forward folds
- [ ] 11.2 Implement `grid` search
- [ ] 11.3 Implement `random` search with seeded RNG
- [ ] 11.4 Implement `bayesian` search via Tree-structured Parzen Estimator (in-house, reference Optuna's implementation only)
- [ ] 11.5 Apply objective spec for scoring: lexicographic, weighted_sum, pareto frontier
- [ ] 11.6 Reject candidates that violate hard constraints or fall below `oos_min_score`
- [ ] 11.7 Implement the rationale generator (LLM pass over optimizer surface + KB neighborhood) producing natural-language justification
- [ ] 11.8 Determinism: seeded across all methods; replay produces identical sequences
- [ ] 11.9 Tests: grid exhaustive, random determinism, TPE convergence on a synthetic objective, rationale presence

## 12. Reference Smoke Strategy

- [ ] 12.1 Reimplement the VXX volatility-range strategy under the new `Strategy` trait
- [ ] 12.2 Author its objective spec (primary metric, secondary constraints, walk-forward)
- [ ] 12.3 Run an end-to-end smoke: data fetch → engine batch → ledger record → KB-aware hypothesis loop → tester → engine → verdict
- [ ] 12.4 Capture the smoke run as a recorded fixture for regression testing

## 13. Cross-Cutting

- [ ] 13.1 Add a top-level CLI (`strategy-gpt`) exposing common operations: ingest, fetch, run, hypothesize, optimize, replay
- [ ] 13.2 Add structured logging end-to-end (Rust `tracing` + Python `structlog`) with run-id correlation
- [ ] 13.3 Add CI: invoke `make lint` and `make test` (canonical entry points defined by the `add-lint-precommit` change), plus end-to-end smoke on a tiny fixture dataset
- [ ] 13.4 Document the developer workflow in `CLAUDE.md` and a top-level `README.md`
- [ ] 13.5 Tag a `v0.1.0` once the smoke run in 12.3 passes
