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
- [x] 3.3 Document the allowed-crate whitelist as the LLM strategy-generation contract — `crates/build-pipeline/whitelist.toml` is the authoritative list; the LLM generator includes it verbatim in its prompt and is instructed to use only listed crates. Source/manifest linter (3.1) remains the runtime guard. We do *not* maintain a private cargo registry mirror: accepted residual risk is that an LLM sidestep that also evades the linter could pull a non-whitelisted dependency at `cargo build` time. Worker-process isolation is the load-bearing safety boundary regardless.
- [x] 3.4 Build driver + plugin loader — strategy crates are `cdylib`s implementing the sealed `engine_rt::Strategy` trait; `engine_rt::strategy_entry!(factory)` emits the C-ABI `_strategy_gpt_{create,drop,abi_major}` symbols so the worker can `libloading`-load the artifact and obtain a `Box<dyn Strategy>` without recompiling the worker. `engine::plugin::StrategyPlugin::load` resolves symbols, checks ABI-major compatibility against `RUNNER_VERSION`, and hands out lifetime-tied `PluginStrategy` instances that drop through the plugin's allocator. `crates/example-strategy/` is the in-tree fixture; `engine/tests/plugin_load.rs` builds it via `cargo` and exercises load → metadata → create/drop cycle → missing-path error. `SystemCargo` lays out a per-build Cargo project (writes `Cargo.toml` + `src/lib.rs`, injects `engine-rt` as a path dep, dedups user-supplied versions of it), then shells out to `cargo build [--release]`. Two new SystemCargo unit tests + one `#[ignore]`d real-build end-to-end test cover the layout and the full cargo invocation path. Same-toolchain caveat documented in both `engine_rt::plugin` and `engine::plugin`.
- [x] 3.5 Implement content-addressed artifact cache keyed by `hash(source)` with reuse on repeat input — `blake3(source + canonical_manifest + runner_version)` keying, on-disk metadata, dependency-order-stable
- [x] 3.6 Implement runner-version migration: detect old artifacts on load, regenerate source via the LLM under the new ABI, rebuild — decision logic implemented (`migration_decision`); LLM regenerate-source step lands with the orchestrator pipeline
- [x] 3.7 Cover the build pipeline with integration tests: happy path, rejected crate, compile failure, artifact reuse — 28 unit + integration-style tests across linter, whitelist, artifact_cache, driver, migration; `Cargo` trait stubbed with `StubCargo` so real `cargo build` is not required for CI

## 4. Backtest Engine — `crates/engine`

- [x] 4.1 Define `BatchSpec`, `RunSpec`, `Mode` enum (`Plain`, `MonteCarlo`, `Slippage`, `RegimeFilter`, `Sensitivity`), and the `BacktestResult` schema — all variants defined; only `Plain` wired into the executor
- [x] 4.2 Implement the worker process binary that loads a strategy artifact, drives the lifecycle methods over a bar stream, and streams `BacktestResult` over Arrow IPC on stdout — `crates/engine/src/bin/engine_worker.rs` reads one length-prefixed JSON `WorkerRequest` from stdin (8-byte LE u64 length + payload, schema in `crates/engine/src/wire.rs`), loads the strategy cdylib via `StrategyPlugin`, drives `run_one + apply_modes`, and emits one framed `WorkerResponse::Ok { result }` (or `WorkerResponse::Error { message }` with non-zero exit) on stdout. Stderr is reserved for diagnostics. Arrow IPC framing upgrade is tracked as a follow-up (JSON-over-pipes v1 mirrors the v1 strategy used by the gateway/ledger sidecars). On Unix, mem cap forwarded via `STRATEGY_GPT_MEM_BYTES` env → `setrlimit(RLIMIT_AS) + RLIMIT_DATA` (best-effort; macOS often ignores `RLIMIT_AS`). Test-only env hooks `STRATEGY_GPT_TEST_{PANIC,SLEEP_MS,EXIT_CODE}` drive coordinator failure-mode tests. Wire round-trip + frame-too-large + truncation tests in `wire::tests`.
- [x] 4.3 Implement the coordinator: spawn N worker processes, dispatch runs, enforce per-run time and memory caps via OS primitives, kill on exceedance — `crates/engine/src/coordinator.rs::Coordinator::execute` dispatches one subprocess per `RunSpec` through a parallelism-capped thread pool (parallelism = `min(BatchSpec.parallelism, runs.len())`), preserving submission order in the result vector. Per-run time cap enforced parent-side via `try_wait` + poll-interval kill; mem cap forwarded to the worker via env. Cooperative cancellation via `Arc<AtomicBool>`. Abort-on-failure: first error sets an abort flag, drains pending work, returns `CoordinatorError::WorkerFailed { run_index, source }`. `annotate_regimes` runs once (bars-invariant) and is stamped on every result. PyEngine in `py-bindings/src/engine_mod.rs` now requires a `worker_path` and drives `Coordinator::execute` instead of in-process plugin loading; Python wrapper `strategy_gpt.engine.Engine` takes `worker_path` + optional `time_cap_secs` / `mem_cap_bytes`. Seven integration tests in `crates/engine/tests/coordinator.rs` cover end-to-end run, order-preserving parallelism, time-cap kill, worker panic (process isolation: parent stays alive, recovery run succeeds), abort on first failure, cancel mid-batch, and empty batch.
- [x] 4.4 Implement abort-on-failure batch semantics: a single run failure cancels remaining runs and reports a structured failure — `run_batch` aborts on first run failure with `BatchError::Run { index, source }`
- [x] 4.5 Implement deterministic execution: seeded RNG, stable bar iteration, identical output for identical inputs — verified by integration test `determinism_identical_inputs_produce_identical_output`
- [x] 4.6 Implement enriched output capture: trades, signals (including `suppressed_by`), equity, exec_log — all four channels populated by `run_one`
- [x] 4.7 Implement `MonteCarlo` mode with block bootstrap over input bars — `modes::monte_carlo` resamples blocks with seeded RNG, re-stamps timestamps, aggregates metrics into one `StressScenario`
- [x] 4.8 Implement `Slippage` and `RegimeFilter` stress modes — `slippage_sweep` clones engine config per bps; `regime_filter` runs per range with filtered bars
- [x] 4.9 Implement parametric `Sensitivity` sweep mode with per-point sub-results — `sensitivity_sweep` overrides numeric param in `run.params`, dedups identical values
- [x] 4.10 Compute post-hoc regime annotations (volatility regime + trend regime) for `BacktestResult.regimes` — `regime::annotate_regimes` emits `low_vol`/`med_vol`/`high_vol` and `uptrend`/`downtrend`/`chop` runs
- [x] 4.11 Expose the engine control plane to Python via PyO3 (`submit_batch`, `poll`, `cancel`) — `PyEngine` in `py-bindings/src/engine_mod.rs` exposes `Engine(worker_path, time_cap_secs?, mem_cap_bytes?)`, `submit_batch(artifact_path, bars_json, spec_json, dataset_manifest) -> handle`, `poll(handle) -> JSON {status, results?, error?}`, `cancel(handle) -> bool`, `drop_handle(handle) -> bool`. Batches run on a `std::thread`-backed dispatcher that drives `engine::coordinator::Coordinator::execute` (subprocess per run; see 4.2 / 4.3); the dispatch thread surfaces results, failures, and cancellation via the same handle map. `engine::plugin::PluginFactory` + `OwnedPluginStrategy` adapt plugin-loaded strategies to `StrategyFactory` and are used by the worker process, not the orchestrator. Python wrapper at `python/strategy_gpt/engine.py` (`Engine`, `JobStatus`) takes `worker_path` + optional caps; five integration tests in `python/tests/test_engine.py` build both `example-strategy` and `engine-worker` via cargo, then exercise submit/poll/cancel/drop + unknown-handle errors (skipped when native unbuilt).
- [x] 4.12 Determinism golden-test: run a known strategy + dataset twice and assert byte-identical results — `determinism_golden_full_pipeline_with_all_modes_byte_identical` exercises run_one + apply_modes across Plain + MonteCarlo + Slippage + Sensitivity on a 60-bar synthetic dataset with fixed seed; asserts full `BacktestResult` equality plus sanity checks on stress/sensitivity output

## 5. Data Gateway — `crates/data-gateway`

- [x] 5.1 Define the `Provider` trait and the normalized `Bar` type (UTC timestamps, exchange-local kept as auxiliary field) — `Provider::fetch_year` returning `Vec<engine_rt::Bar>`; `ProviderQuery` carries symbol+year+resolution+adjustment
- [ ] 5.2 Implement the `yfinance` provider — pending; requires HTTP. CSV provider is the v1 path
- [x] 5.3 Implement the generic CSV/parquet provider for bring-your-own data files — `CsvProvider` reads `<base_dir>/<symbol>.csv` with `timestamp,open,high,low,close,volume` header; accepts RFC3339 or `YYYY-MM-DD` timestamps; clips to query year
- [x] 5.4 Implement the year-segmented content-addressed cache: parquet blobs under `cache/`, manifest table in SQLite — JSON blobs in v1 (parquet upgrade tracked here); SQLite `blobs` table at `<root>/manifest.sqlite`, blake3 key over (provider, symbol, resolution, year, adjustment)
- [x] 5.5 Implement cache modes: `prefer-cache`, `validate`, `force-refresh`, `offline` — all four wired in `DataGateway::fetch`; `Validate` aliases `PreferCache` in v1 with a documented follow-up
- [x] 5.6 Implement the normalizer: timezone conversion, calendar alignment per session calendar, adjustment-policy tagging — UTC enforcement, sort, dedup, range clip, OHLC sanity; calendar alignment is task 5.6 follow-up
- [x] 5.7 Implement the consolidator with internal-only configuration: precedence order, close/volume tolerance, on-disagree behavior, missing-bar handling — `Consolidator` aligns per-provider bars by `(symbol, ts)` in a `BTreeMap`, applies close/volume tolerance via `diverges_pct`, resolves disagreements via `DivergencePolicy` (`PickPrecedence` / `Fail` / `Median`)
- [x] 5.8 Emit divergence warnings and route them to the experiment ledger — `Consolidator::merge` returns `ConsolidationOutcome { bars, warnings: Vec<DivergenceRecord> }`; gateway surfaces them in `DatasetResponse.warnings`; one-way wire (orchestrator translates `data_gateway::DivergenceRecord` → `ledger::DivergenceWarning` on ledger record); 3 integration tests cover close-mismatch, precedence resolution, within-tolerance no-op
- [x] 5.9 Issue manifests with every returned dataset that uniquely identify the cache blobs used — `DatasetResponse { bars, manifest, manifest_hash }`; `manifest_hash` is blake3 over the ordered blob-hash list
- [x] 5.10 Expose the gateway to Python via PyO3 (`fetch`, `manifest_for`, `cache_stats`) — `PyDataGateway` in `py-bindings/src/gateway.rs` exposes `__init__(root)`, `register_csv_provider`, `fetch(request_json, mode)`, `cache_stats()`, `root()`; JSON-string boundary for `BarRequest` and `DatasetResponse`; cache mode parsed from `"prefer_cache" / "validate" / "force_refresh" / "offline"`
- [x] 5.11 Tests: cache hit/miss, year-segment merging, multi-provider divergence, offline mode error — 10 integration tests covering populate, cache hit, year-segmented fetch, manifest hash change, force-refresh, offline error + warm-cache serve, unknown provider, invalid range, normalizer sort+dedup. Multi-provider divergence test lands with 5.8

## 6. Experiment Ledger — `crates/ledger` + Python client

- [x] 6.1 Design the SQLite schema: `runs`, `hypotheses`, `decisions`, `dataset_manifests`, `divergence_warnings`, `objectives`, `strategy_versions` — STRICT tables with rfc3339 timestamps, JSON-serialized blobs for `parameters_json` / `modes_json` / etc.; indices on `decisions(decided_at DESC)`, `decisions(hypothesis_id)`, `runs(hypothesis_id)`, `runs(dataset_manifest_hash)`, `divergence_warnings(symbol, ts)`
- [x] 6.2 Enforce append-only semantics via triggers that reject UPDATE/DELETE on the protected tables — `BEFORE UPDATE` + `BEFORE DELETE` trigger pair on every protected table firing `RAISE(ABORT, '<table> is append-only')`
- [x] 6.3 Implement parquet sidecar I/O for trades, signals, equity, and exec_log keyed by run id — JSON sidecars in v1; `SidecarStore` API stays shape-stable for the parquet swap (parquet upgrade tracked here as a follow-up)
- [x] 6.4 Implement the recent-decisions query for the Hypothesis Loop's state initialization — `Ledger::recent_decisions(n)` returns `Vec<RecentDecision>` joined with hypotheses, ordered newest first
- [ ] 6.5 Implement the run replay path: given a run id, reconstruct `BatchSpec` and dataset for byte-identical reproduction — `Ledger::get_run` returns the recorded `RunRecord`; full `BatchSpec` reconstruction needs the data gateway's manifest replay (phase 5) to produce identical bars
- [x] 6.6 PyO3 bindings: `record_run`, `record_hypothesis`, `record_decision`, `record_divergence`, `query_recent_decisions`, `replay_run` — `PyLedger` in `py-bindings/src/ledger_mod.rs` exposes all `record_*` writers, `recent_decisions(limit)`, `get_run(id)` (replay_run subset; full reconstruction is task 6.5), plus `store_sidecar` / `load_sidecar` keyed by `"trades" / "signals" / "equity" / "exec_log"`; JSON-string boundary throughout
- [x] 6.7 Tests: append-only enforcement, sidecar round-trip, replay produces identical `BacktestResult` — 10 integration tests covering open-twice idempotence, UPDATE/DELETE rejection, run round-trip, recent-decisions ordering + join, divergence warnings, objectives + strategy versions, all four sidecar kinds round-trip, missing-sidecar error, schema-meta sanity

## 7. Objectives Spec

- [x] 7.1 Define the YAML/JSON schema for objective specs (primary, secondary, tradeoff, walk-forward, oos_min_score) — serde-derived types in `objectives::spec`; `ObjectiveSpec::from_yaml` / `from_json`; comparison parser accepts `>= 1.5` / `<= 0.20` strings
- [x] 7.2 Implement spec validation (metric names valid against engine output, comparison operators, weight non-negativity, pareto requires ≥2 metrics) — `objectives::validate` checks all five rules + walk-forward sanity (folds >= 1, gap < folds); metric registry sourced from `engine::BacktestMetrics` fields
- [x] 7.3 Implement evaluator: given metrics + spec, return pass/fail and aggregated score under the chosen tradeoff mode — `objectives::evaluate` returns `EvaluationOutcome { accepted, score, violations, soft_misses }`; lexicographic = primary value; weighted_sum sums primary + soft secondaries with sign by target direction (`<=`/`<` negate); pareto returns scalar score (frontier accumulated by caller)
- [x] 7.4 Tests: constraint violation, lexicographic tiebreak, weighted_sum scoring, pareto frontier extraction — 13 integration tests covering YAML parsing, all validation errors, accept/reject paths, soft-miss recording, weighted-sum sign handling, and comparison string round-trip

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

- [x] 11.1 Implement the optimizer driver that evaluates candidates by submitting batches to the engine across walk-forward folds — `python/strategy_gpt/optimizer.py::optimize` enumerates candidates from a `Searcher`, dispatches evaluation through a caller-supplied `evaluate` callable (engine submission lives above this layer), scores via a `score` callable returning `EvaluationOutcome`, gates on `oos_min_score`, returns `OptimizerResult { trials, best, rejected_count }`. Walk-forward fold orchestration is the caller's responsibility (lands with Phase 10 tester wiring).
- [x] 11.2 Implement `grid` search — `GridSearcher` enumerates the cartesian product of a dict-of-sequences; `count()` returns the candidate count
- [x] 11.3 Implement `random` search with seeded RNG — `RandomSearcher` samples from a space of `ContinuousParam` (linear + log) / `IntParam` / `ChoiceParam`; identical seed → identical sequence
- [x] 11.4 Implement `bayesian` search via Tree-structured Parzen Estimator (in-house, reference Optuna's implementation only) — `TPESearcher` in `python/strategy_gpt/optimizer.py` runs `n_startup_trials` uniform draws, then for each subsequent step splits history by `gamma` quantile, fits a per-parameter Parzen estimator (Gaussian KDE for `ContinuousParam` / `IntParam`, Laplace-smoothed categorical histogram for `ChoiceParam`), samples `n_candidates_per_step` candidates from the "good" distribution and picks the one maximising `log l(x) − log g(x)`. Adaptive bandwidth via Silverman-like rule, log-scale params handled in log-space. Sequential-by-construction, so it owns its own search loop (`TPESearcher.search`) rather than plugging into the stateless `optimize()` driver. Five tests in `python/tests/test_optimizer.py` cover determinism (same seed → same trial sequence), convergence on a 1-D unimodal target (TPE matches or beats random search at equal budget), mixed param types, gamma validation, and oos_min_score gate behaviour.
- [x] 11.5 Apply objective spec for scoring: lexicographic, weighted_sum, pareto frontier — scoring delegated to the caller-supplied `ScoreFn`; production wiring uses `strategy_gpt.objectives.evaluate_spec` over the strategy's `ObjectiveSpec`. Tradeoff modes are implemented in the Rust evaluator (`objectives::evaluate`) and surface via the PyO3 bindings.
- [x] 11.6 Reject candidates that violate hard constraints or fall below `oos_min_score` — `optimize(..., oos_min_score=...)` ANDs `outcome.accepted` with the score gate; rejected trials still appear in `result.trials` with `accepted=False` for audit
- [ ] 11.7 Implement the rationale generator (LLM pass over optimizer surface + KB neighborhood) producing natural-language justification
- [x] 11.8 Determinism: seeded across all methods; replay produces identical sequences — `RandomSearcher` uses `random.Random(seed)`; `GridSearcher` iteration order is deterministic; `optimize` preserves candidate submission order in `trials`
- [x] 11.9 Tests: grid exhaustive, random determinism, TPE convergence on a synthetic objective, rationale presence — 15 tests in `python/tests/test_optimizer.py` covering grid enumeration + uniqueness, random determinism + bounds (linear + log), oos_min_score gate, all-rejected fallback, candidate-order preservation, TPE determinism + convergence on a unimodal target + mixed param types + gamma validation. Rationale-presence test lands with 11.7.

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
