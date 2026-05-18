# Architecture

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

## Trust boundaries

Two boundaries are load-bearing:

- **PyO3 boundary** — the Python orchestrator only calls into trusted Rust crates that the team owns (gateway, ledger, kb, engine control plane, build pipeline). No LLM-emitted code ever runs in-process. See [ADR 0003](../decisions/0003-pyo3-trusted-crate-boundary.md).
- **Worker boundary** — every LLM-compiled strategy executes in a separate `engine-worker` subprocess. A panic, OOM, or timeout in a strategy kills the worker, not the orchestrator. Process isolation is the safety boundary; there is no in-process sandboxing. See [ADR 0004](../decisions/0004-engine-worker-subprocess-arrow-ipc.md) and [ADR 0005](../decisions/0005-worker-process-isolation-no-sandbox.md).

## Data flow (one loop iteration)

```
1. data-gateway      fetch + cache + normalize OHLCV bars
2. engine            run baseline BatchSpec against the current strategy
3. ledger            record run + BacktestResult (append-only)
4. hypothesis-loop   diagnose → kb_query → generate → critique → rank → select
                     ├─ KB hybrid retrieval (graph + vector)
                     ├─ reasoning model (Anthropic / OpenAI)
                     └─ persist accepted/rejected decisions to the ledger
5. tester            translate hypothesis → parameter diff OR new Rust source
                     ├─ build-pipeline: lint, allowed-crate check, cargo build
                     ├─ smoke backtest on a small slice
                     └─ full BatchSpec to the engine (walk-forward + stress)
6. engine + ledger   record verdict, link back to the hypothesis id
7. (loop)            verdict feeds the next diagnose pass
```

Reproducibility invariant: every run pins the strategy-artifact hash, dataset-manifest hash, parameters, modes, seed, and runner version. Identical inputs produce byte-identical `BacktestResult`s. The ledger plus the local cache are sufficient to byte-identically reproduce any historical run via `strategy-gpt replay --run-id <id>` (see `openspec/specs/experiment-ledger/spec.md`).

## Module map

| Stage | Crate / module | Spec |
|---|---|---|
| Fetch, cache, consolidate | `crates/data-gateway` | `openspec/specs/data-gateway/spec.md` |
| Backtest | `crates/engine` + `engine-rt` | `openspec/specs/backtest-engine/spec.md` |
| Ledger | `crates/ledger` | `openspec/specs/experiment-ledger/spec.md` |
| Knowledge base | `crates/kb` (hybrid graph + vector over SQLite) | `openspec/specs/knowledge-base/spec.md` |
| Hypothesis loop | `python/strategy_gpt/hypothesis_loop.py`, `nodes.py`, `diagnose.py`, `kb_query.py` | `openspec/specs/hypothesis-loop/spec.md` |
| Tester | `python/strategy_gpt/tester.py` | `openspec/specs/tester/spec.md` |
| Optimizer | `python/strategy_gpt/optimizer.py` | `openspec/specs/param-optimizer/spec.md` |
| Objective specs | `crates/objectives` + per-strategy `objective.yaml` | `openspec/specs/objectives/spec.md` |
| Build pipeline | `crates/build-pipeline` (incl. `whitelist.toml`) | `openspec/specs/strategy-runtime/spec.md` |

## Strategies: lifecycle

Strategies are **Rust `cdylib` crates** that implement the sealed `engine_rt::Strategy` trait. They are loaded by the engine worker at runtime via `libloading`; they do not link statically to the orchestrator.

Every strategy implements five lifecycle methods (see `openspec/specs/strategy-runtime/spec.md`):

- `metadata()` — name, version, author, description.
- `on_init(ctx)` — once before any bars; pull `__params__` out of state.
- `on_bar(bar, ctx)` — once per bar in chronological order; this is where signals are evaluated and orders are submitted.
- `on_fill(fill, ctx)` — when a previously submitted order fills.
- `on_end(ctx)` — once after the last bar.

The only way a strategy talks to the engine is the `Context` capability handle:

- `submit_order(symbol, side, size, limit_price, stop_price, reason)`
- `get_position(symbol)` — accounting view (size + avg price). No P&L exposed to running strategies.
- `log_signal(name, value, fired, suppressed_by)`
- `log_decision(event, details)`
- `read_indicator(name)` — engine-provided indicators
- `state_get(key) / state_set(key, value)` — engine-managed state for reproducibility

`Context` deliberately exposes no filesystem, network, syscall, broker, or order-cancellation surface. There is no `cancel_order`: a strategy that wants to flip submits a closing intent on the next bar.

## Two ways strategies get authored

1. **Human-authored**, like `crates/vxx-strategy/`. You write `src/lib.rs`, implement `Strategy`, and call the `strategy_entry!(factory)` macro at the bottom of the file. The macro is the *only* `#[no_mangle] extern "C"` surface; you never write `unsafe` yourself.
2. **LLM-authored**, through the build pipeline. The hypothesis loop's Tester (`python/strategy_gpt/tester.py`) hands an LLM a hypothesis plus the strategy-generation prompt. The LLM emits Rust source. The build pipeline lints it (parse check + allowed-crate enforcement against `crates/build-pipeline/whitelist.toml`), then `cargo build`s it with `sccache` and content-addressed caching. Successfully built artifacts are stored keyed by `hash(source)` and reused on subsequent identical builds.

The allowed-crate whitelist is the dependency-surface guard for LLM-emitted code. It is human-curated, versions are *not* pinned, and any direct or dev/build dependency outside the list causes the linter to reject the strategy before `cargo build` is even invoked. See `crates/build-pipeline/whitelist.toml`.

Strategy artifacts are versioned against the runner ABI (`RunnerVersion` in `engine-rt`). The runtime carries a **single** ABI version at any time; a major version bump triggers re-generation of every artifact from source through the LLM. There is no multi-version compatibility shim — see [ADR 0006](../decisions/0006-sealed-strategy-trait.md).

## Module roles (durable contract)

- **Data Gateway** — multi-provider fetch with year-segmented content-addressed cache, internal-only consolidation policy, divergence warnings to the ledger.
- **Backtest Engine** — batched, deterministic, abort-on-failure, native stress and sensitivity modes, enriched output schema (trades, signals incl. `suppressed_by`, equity, exec_log, regimes).
- **Strategy Runtime (`engine-rt`)** — sealed `Strategy` trait, `Context` capability handle, `RunnerVersion`, semver, no backwards compatibility.
- **Build Pipeline** — lint, allowed-crate whitelist (no version pinning), `cargo build` with sccache, content-addressed artifact cache.
- **Hypothesis Loop** — LangGraph workflow (`diagnose`, `kb_query`, `generate`, `critique`, `rank`, `select`) with internal iteration and persisted decision log.
- **Tester** — translate hypothesis to artifact, run lint + smoke + full batch, report verdict against falsification criterion.
- **Parameter Optimizer** — in-house per-fold search over the experiment-spec fold scheme, multi-metric objectives, overfitting-aware selection layer, LLM-generated rationale.
- **Knowledge Base** — hybrid graph + vector retrieval over a SQLite-backed store, curated ingestion, citation-friendly retrieval.
- **Experiment Ledger** — SQLite append-only + parquet sidecars; sufficient (with cache) to byte-identical reproduce any run.

## Repo layout

```
crates/                 Rust workspace
  engine-rt/            Strategy trait, Context capability handle, RunnerVersion, strategy_entry! macro
  engine/               BatchSpec, coordinator, worker, modes (Plain / MonteCarlo / Slippage / RegimeFilter / Sensitivity)
  data-gateway/         providers, year-segmented content-addressed cache, normalizer, consolidator
  ledger/               SQLite append-only + parquet sidecars
  kb/                   hybrid retrieval (graph + vector) over a SQLite-backed store
  build-pipeline/       lint, allowed-crate whitelist enforcement, cargo build with sccache + content-addressed artifact cache
  objectives/           objective-spec parsing + validation (per-strategy YAML)
  py-bindings/          PyO3 module exposing trusted crates as `strategy_gpt._native`
  vxx-strategy/         Reference VXX volatility-range smoke strategy (cdylib + objective.yaml)
  example-strategy/     No-op fixture for plugin-loader tests

python/strategy_gpt/    Orchestrator
  cli.py                Typer CLI: fetch / cache-stats / recent-decisions / replay / run / ingest / hypothesize / optimize
  gateway.py            Data gateway shim
  engine.py             Engine submit_batch / poll
  ledger.py             Ledger reads + replay
  kb.py / kb_query.py   KB client + the hypothesis-loop kb_query node
  hypothesis_loop.py    LangGraph state schema, termination semantics, decision-log bootstrap
  nodes.py              diagnose / generate / critique / rank / select node implementations
  diagnose.py           Diagnosis logic + result projection
  tester.py             Hypothesis → artifact translation, lint + smoke + full-batch pipeline, verdict
  optimizer.py          Per-fold search drivers over walk-forward folds
  search/               One file per search method, registered via __init__.py
  objectives.py         Pydantic mirror of the YAML objective spec
  rationale.py          LLM-grounded rationale generator (consults KB)
  reasoning.py          Reasoning-model client (Anthropic / OpenAI)
  types.py              Cross-FFI types mirroring the Rust serde records
  smoke.py              End-to-end recorded smoke run + SmokeReport fixture

kb/                     Curated source list, starter corpus, recorded fixtures
cache/                  Year-segmented content-addressed parquet (gitignored)
ledger/                 SQLite ledger + parquet sidecars (gitignored)
openspec/               Change proposals and capability specs
docs/                   This documentation tree
```
