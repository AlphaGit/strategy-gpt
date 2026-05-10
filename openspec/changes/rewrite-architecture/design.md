## Context

The current strategy-gpt is a single-process Python prototype that uses `exec` on LLM-generated code, has no persistent ledger, no real backtest engine, and a broken outer loop. The core insight — an LLM-driven hypothesis/test/accept loop over quantitative trading strategies — is sound. The implementation is not.

**Scope of the system.** strategy-gpt is a research platform whose product is the *creation and testing* of strategies, not their live operation. The engine simulates fills inside a backtest purely so hypotheses can be evaluated on historical data; there is no live order book, no broker integration, no real-time position management. Anywhere this document mentions orders, fills, positions, or risk caps, those are *backtest-side* concepts evaluated against historical bars, not live-trading machinery.

This design specifies a polyglot rewrite: a Rust core for everything performance-critical or contract-heavy (data fetching, backtest execution, strategy ABI), and a Python orchestration layer for everything reasoning-heavy (LangGraph hypothesis loop, evaluator, ingestion glue). The two halves communicate via PyO3 in-process for trusted code and subprocess + Arrow IPC for strategy execution.

Stakeholders are the project maintainer (working alone for now) and any future quant collaborators. There are no production users. There is no live trading. There is no UI. Reproducibility, modularity, and the integrity of the experimental record are the dominant non-functional requirements.

## Goals / Non-Goals

**Goals:**

- Strict module boundaries with typed, narrow contracts between LLM-driven roles (Evaluator, Ideator/Hypothesis, Tester) and execution machinery (Engine, Data Gateway).
- Strategies are first-class native Rust artifacts with a stable trait contract, semver-versioned, with no dual-version maintenance burden.
- Backtests are reproducible: every run pins a strategy artifact hash, a dataset manifest, parameters, modes, and a seed. The same inputs always produce the same outputs.
- Stress and sensitivity testing are first-class engine modes, not external scripts.
- An append-only experiment ledger captures every hypothesis, decision (accept/reject with reason), strategy diff, run config, and verdict.
- The hypothesis module reasons with the help of a curated knowledge base (graph + vector retrieval over books/papers) and persists a decision log so it can learn its own taste over time.
- The system is offline-capable: market data is locally cached and reused without external calls when possible.
- Multi-metric objectives are declared per strategy and consumed uniformly by evaluator and optimizer.

**Non-Goals:**

- Live trading, broker order routing, paper trading, real-time position management. The engine simulates fills inside a backtest purely to evaluate hypotheses; positions are accounting state, not a live book.
- Sandboxing of LLM-generated strategy code. Strategies execute in a separate worker process for reliability (a crash does not take down the orchestrator), but with no syscall/filesystem/network restrictions. Trust boundary: strategy code originates from our own LLM pipeline, not third parties.
- Backward compatibility for old strategy artifacts when the runner version increments. Old artifacts are detected and migrated; the runner does not maintain multiple ABIs in parallel.
- Distributed execution across machines. Engine workers are local processes only.
- Web UI, REST API, multi-tenant access control.
- Generic execution engine usable outside the research loop. The engine is built for batched backtests with the output schema this loop needs.
- Wrapping `nautilus_trader` or any other backtest framework. Greenfield engine.

## Decisions

### Decision 1: Polyglot — Rust core + Python orchestration

**Choice:** Performance-critical or contract-heavy code is Rust. LLM orchestration, prompt graphs, and ingestion glue are Python.

**Why:** Rust gives memory safety, deterministic performance, and a strong type system for the engine and data gateway, where bugs are catastrophic and silent. Python gives best-in-class LLM tooling (LangGraph, anthropic/openai SDKs, embedding clients, prompt observability), where iteration speed and interoperability with reasoning models matter more than throughput.

**Alternatives considered:**

- *Pure Python.* Rejected: backtest performance ceiling too low for batched stress/sensitivity sweeps, and `exec` of LLM code stays an in-process problem.
- *Pure Rust.* Rejected: LangChain/LangGraph ecosystem in Rust is immature; reasoning model integration would be hand-rolled and brittle.
- *Go core.* Rejected: GC pause behavior worse for tight backtest loops; weaker numeric ecosystem than Rust.

### Decision 2: PyO3 in-process for trusted code; subprocess for strategy execution

**Choice:** Data Gateway, KB client, ledger, and engine *control plane* expose PyO3 bindings called in-process from Python. Strategy *execution* runs in a separate Rust worker process spawned by the engine; results stream back via Arrow IPC.

**Why:** PyO3 is fast, type-checked, and avoids subprocess startup tax for hot calls (data fetch, KB query, ledger write). Strategy execution is moved to a worker process so a crash in LLM-written code does not take the orchestrator with it, and so that the engine can enforce time/memory caps per run via OS primitives (rlimit, kill).

**Alternatives considered:**

- *Pure CLI + Arrow files.* Rejected: 50–100 ms process startup tax per call dominates ledger/KB read patterns.
- *gRPC service mesh.* Rejected: overkill for a single-host system, adds operational complexity.
- *Strategies as in-process dlopen of cdylib (no worker process).* Rejected by the project owner: process boundary is desired for crash isolation even though full sandboxing is out of scope.

### Decision 3: Strategies as native Rust, no sandboxing

**Choice:** The LLM emits Rust source. It is compiled (with sccache, against a whitelisted crate registry) into a strategy artifact and loaded by a worker process. The worker has no syscall, filesystem, or network restrictions.

**Why:** Native Rust gives full expressivity and ecosystem access — important because the platform must accommodate strategies of arbitrary shape. The owner explicitly accepted that no sandboxing is in scope; strategies are produced by our own pipeline, not third parties.

**Alternatives considered:**

- *WASM strategies.* Rejected: complexity not justified given the trust model. Revisit if/when external authors submit strategies.
- *Declarative DSL or hybrid config + expressions.* Rejected: would cap expressivity for unusual strategies; the project explicitly wants flexibility.

### Decision 4: Strategy ABI — semver, no backward compatibility

**Choice:** The `Strategy` trait and `Context` API are versioned. Each strategy artifact records the runner version it was built against. When the runner increments to a new major version, old artifacts are detected, flagged, and migrated by re-emitting source through the LLM and recompiling. The runtime never carries multiple ABIs.

**Why:** Maintaining multiple ABI versions is expensive and the project is small. Git history preserves old runners if needed for forensic reproduction.

**Alternatives considered:**

- *Permanent backward compatibility.* Rejected: would calcify the trait surface and slow evolution.
- *No versioning at all.* Rejected: would cause silent miscompilation when the trait changes.

### Decision 5: Batched backtests, abort-on-failure

**Choice:** The Tester emits a `BatchSpec { strategy_artifact, dataset, runs: [{params, modes, slice, seed}, ...] }` to the engine. The engine compiles the strategy once and runs all configurations across a worker pool. Stress modes and sensitivity sweeps are extra entries in the `runs` list.

A run that fails (panic, OOM, timeout) aborts the entire batch and records the failure in the ledger. There is no resume.

**Why:** Compile once / run many amortizes Rust build time over many configurations. Walk-forward folds, parameter sweeps, and Monte Carlo iterations all become entries in the same batch, parallelizing trivially. Abort-on-failure simplifies the engine and matches owner preference.

**Alternatives considered:**

- *Run-level retry/resume.* Rejected: complicates state machine; cheaper to rerun batch from scratch.
- *One process per run.* Rejected: process startup dwarfs short backtests.

### Decision 6: Engine output schema — enriched, structured

**Choice:** A `BacktestResult` is a research artifact (not a trade-blotter equivalent) and includes:

- `metrics`: Sharpe, Sortino, Profit Factor, Win Ratio, Max Drawdown, Annualized Return, trade-length stats.
- `trades`: every closed simulated trade with `entry_ts`, `exit_ts`, `side`, `size`, `pnl`, `reason_in`, `reason_out`, snapshot of active signals.
- `signals`: every signal evaluation with `ts`, `name`, `value`, `fired`, optional `suppressed_by`.
- `equity`: equity, drawdown, exposure curve.
- `regimes`: post-hoc regime annotations.
- `exec_log`: ordered decision events (entry skipped, filter blocked, sanity-bound hit, hedge resized, etc.).
- `stress` / `sensitivity`: optional sub-results when those modes ran.
- `meta`: strategy hash, dataset manifest hash, seed, runner version.

**Why:** The hypothesis module's reasoning quality is bounded by what the engine emits. Suppressed-signal tracking lets the hypothesis module reason about over-aggressive filters and near-misses. The decision log lets it understand *why* a simulated trade was or was not taken.

### Decision 7: Data Gateway — content-addressed cache, year-segmented, internal consolidator config

**Choice:** Every successful provider fetch is normalized (UTC, calendar-aligned, adjustment-policy-tagged) and stored as parquet keyed by `hash(provider, symbol, resolution, year, adjustment_policy, version)`. The cache is segmented by calendar year so walk-forward slices and overlapping requests reuse prior fetches at year granularity. A manifest table records every cached blob.

The consolidator merges multiple providers for the same `(symbol, resolution, range)` and emits divergence warnings (close mismatch, volume mismatch, missing bar, tz/calendar mismatch) to the ledger. Consolidation policies (precedence order, tolerances, on-disagree behavior, missing-bar handling) are an **internal configuration of the consolidator**, not per-request parameters. Strategies and tests do not pick policies; they get consolidated data and warnings.

**Why:** Year-segmentation balances cache granularity (too small = many tiny files, too coarse = poor reuse) with walk-forward access patterns. Internal-only consolidation policy keeps the call surface narrow and makes the data layer's behavior predictable across the codebase.

**Alternatives considered:**

- *Day-segmented cache.* Rejected: produces too many files for multi-year requests.
- *Per-request consolidation policies.* Rejected by owner: the consolidator should have one consistent personality.

### Decision 8: Knowledge Base — Kuzu (graph) + LanceDB (vector), curated ingestion

**Choice:** Two embedded stores. Kuzu holds the property graph (concepts, indicators, regimes, models, techniques, sources, with relations IMPLEMENTS, CONTRADICTS, REQUIRES, GENERALIZES, CITES, EMPIRICAL_SUPPORT, FAILS_IN_REGIME). LanceDB holds dense embeddings of source chunks. Retrieval is hybrid: vector top-k, then graph neighborhood expansion, then re-rank.

The ingestion pipeline is curated: a human-approved list of books, papers, and resources is chunked, embedded, and passed through an LLM extractor that emits entities and relations into the graph with provenance.

**Why:** Both stores are embedded, columnar, and Rust-aligned, matching the rest of the stack. Manual cross-store joins are acceptable at KB scale (thousands of nodes, not millions). The KB is "just a RAG" with a graph layer — the priority is retrieval quality on curated financial knowledge, not graph DB feature breadth.

**Alternatives considered:**

- *Postgres + pgvector + Apache AGE.* Rejected: AGE less battle-tested; Postgres adds a server process.
- *Neo4j + native vector.* Rejected: heaviest option; serves a use case we do not have.
- *DuckDB + LanceDB + edges table.* Rejected: no real graph queries; closes off future multi-hop reasoning.

### Decision 9: Hypothesis Loop — LangGraph, decision log persisted

**Choice:** A LangGraph workflow with nodes `diagnose`, `kb_query`, `generate`, `critique`, `rank`, `select`. The graph state holds `accepted` and `rejected` hypothesis lists with rationale and KB citations, plus `open` candidates. The loop iterates until at least K hypotheses pass critique, an iteration budget is exhausted, or candidate similarity to prior items crosses a threshold. The final decision log is persisted to the ledger; subsequent runs read prior accepted/rejected decisions as context.

**Why:** LangGraph gives explicit, observable state transitions for a multi-step reasoning workflow. Persisting the decision log closes the feedback loop: the hypothesis module can learn what kinds of changes the system has tried and what worked.

### Decision 10: Tester is separate from Hypothesis

**Choice:** The Tester takes a single hypothesis, translates it into a concrete strategy diff (parameter change) or new strategy source (logic change), runs the build pipeline (lint, compile, smoke-test on a tiny slice), then delegates a full BatchSpec to the engine and reports the verdict.

**Why:** The smoke test catches LLM hallucinations cheaply (compile errors, expression typos, panics on the first bar) before paying for a full backtest. Keeping the Tester separate from the Hypothesis module gives a clean point at which to reject malformed hypotheses without polluting the reasoning loop.

### Decision 11: Parameter Optimizer — in-house

**Choice:** A custom optimizer that supports grid, random, and Bayesian methods (Tree-structured Parzen Estimator initially) over walk-forward folds, consuming the per-strategy objective spec and emitting both an optimized parameter set and a natural-language rationale. The rationale is a separate LLM pass that reads the optimizer's surface and the relevant KB neighborhood.

**Why:** Owner preference. Existing libraries (Optuna, Ax) bring significant API surface, opinionated study state management, and Python-side coupling. An in-house optimizer can be tightly integrated with the engine batch API and the objective spec.

**Alternatives considered:**

- *Optuna.* Rejected per owner: roll our own. We can read Optuna's TPE implementation as reference.

### Decision 12: Multi-metric objectives — parametric per strategy

**Choice:** Each strategy declares an objective spec listing primary metric, secondary metrics with weights or hard constraints, and a tradeoff mode (`lexicographic`, `weighted_sum`, `pareto`). Evaluator (which decides whether a strategy is "good enough") and Optimizer both read the same spec.

**Why:** Different strategies optimize different things. A volatility-harvesting strategy cares about drawdown control over Sharpe; a directional momentum strategy cares about Sharpe over win rate. Parametrizing keeps the loop strategy-agnostic.

### Decision 13: Allowed-crate whitelist, no version pinning

**Choice:** LLM-emitted strategies are restricted to a whitelist of crates (e.g., `polars`, `ndarray`, `chrono`, `serde`, plus our own `engine-rt`). Versions are *not* pinned: the latest version within the whitelist is used. The whitelist is enforced by hosting a local registry mirror or by parsing/rewriting Cargo.toml before build.

**Why:** Whitelist prevents obvious abuse (e.g., `tokio`, `reqwest`, `std::process` shells) and constrains the LLM's choices to crates we have vetted. Skipping version pinning keeps the whitelist easy to maintain; if upstream breaks something, the build fails loudly, we update the strategy, and move on.

**Alternatives considered:**

- *Pinned versions.* Rejected per owner: maintenance overhead not justified.
- *No whitelist, full crates.io.* Rejected: invites supply-chain and dependency-bloat issues.

### Decision 14: Experiment Ledger — SQLite, append-only

**Choice:** SQLite database. Tables for `runs`, `hypotheses`, `decisions`, `dataset_manifests`, `divergence_warnings`, `objectives`, `strategy_versions`. Trades and equity curves are stored as parquet sidecars referenced by run id (SQLite is the index, parquet holds bulk arrays).

**Why:** Embedded, transactional, well-understood. Sufficient for a single-user research system. Parquet sidecars keep large arrays out of the SQL file and keep DB size manageable.

## Risks / Trade-offs

- **Risk: Build pipeline becomes a bottleneck.** Native Rust compile per hypothesis is 5–30 s. → *Mitigation:* `sccache`, persistent build cache directory, content-addressed strategy artifacts so rebuilding the same source is free; batched runs amortize compile across many configurations.
- **Risk: LLM emits Rust that compiles but misbehaves silently** (e.g., panics outside the Tester's smoke test, sizes positions in absurd ways that distort backtest metrics). → *Mitigation:* enriched output schema + decision log makes silent misbehavior detectable; engine applies sanity bounds on backtest position size, exposure, and turnover via `Context` API constraints — these are validity checks against degenerate hypotheses, not live-trading risk management.
- **Risk: KB ingestion quality determines hypothesis module quality.** Garbage in, garbage out. → *Mitigation:* curated source list, human-reviewed extracted entities at v1, expand later.
- **Risk: Walk-forward + cache interaction may produce silent overfitting** if folds inadvertently share data. → *Mitigation:* fold definitions are explicit and recorded in the run manifest; engine rejects overlapping train/test ranges by default.
- **Risk: Survivorship bias in cached data** (e.g., yfinance silently drops delisted symbols). → *Mitigation:* consolidator cross-checks symbol coverage across providers and warns on missing-symbol asymmetry; ledger records which provider was authoritative.
- **Risk: Strategy ABI churn forces frequent migrations.** → *Mitigation:* keep the trait surface narrow; bump major only for breaking changes; record runner version per artifact and run a migration pass automatically.
- **Trade-off: No sandboxing means a buggy strategy can do anything within the worker process** (write files, hang). Process isolation prevents host crash; resource caps prevent runaway. The owner accepted this trade explicitly.
- **Trade-off: In-house optimizer means slower time-to-first-result** vs adopting Optuna. Owner preference; mitigated by starting with grid + random and adding Bayesian later.

## Migration Plan

This is a full rewrite, not an evolution.

1. **Freeze the existing repo at a tagged ref** (`pre-rewrite`). Old code remains accessible via git but is not maintained.
2. **Establish the new repo layout** in place: `crates/` (Rust workspace), `python/` (orchestrator), `kb/` (ingestion), `cache/` (gitignored), `ledger/` (gitignored).
3. **Build bottom-up:** Data Gateway → Backtest Engine + Strategy Runtime → Tester → Hypothesis Loop + Knowledge Base + Experiment Ledger → Parameter Optimizer.
4. **Reimplement the reference VXX volatility-range strategy** under the new contracts as the smoke-test strategy at the end of phase 2.
5. **Cache rebuild:** the existing `cache/` feather files are not migrated. The new content-addressed cache is rebuilt on first use.
6. **No rollback strategy.** If the rewrite is abandoned, the old repo at `pre-rewrite` is the fallback.

## Open Questions

- **KB ingestion specifics.** Source list, chunking policy, entity extraction prompts, graph schema vocabulary. To be resolved in a follow-up `kb-ingestion-pipeline` change.
- **Optimizer rationale prompt design.** How much KB context does the rationale generator need? Probably bounded by token budget; resolve empirically.
- **Walk-forward defaults.** Fold count, anchoring, gap. Likely strategy-class-dependent; defer to per-strategy objective spec.
- **Outer mutation loop.** Hypothesis verdict → adopted strategy mutation → re-run cycle. In scope at the per-iteration level (one verdict updates one strategy version); out of scope as a full autonomous run-until-converged daemon.
- **Provider precedence at v1.** yfinance only, or Polygon/IBKR from day one? Current plan: yfinance + CSV at v1, add Polygon next, IBKR later. To be confirmed in tasks.
- **Reasoning model selection.** Claude Opus / O-class for hypothesis critique; smaller model for diagnose/rank? Leave configurable.
