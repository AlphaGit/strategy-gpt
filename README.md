# strategy-gpt

LLM-driven research loop for **creating and testing** quantitative trading strategies. Polyglot: a Rust core (engine, data gateway, ledger, knowledge base, build pipeline) wrapped by a Python orchestrator (LangGraph hypothesis loop, optimizer, tester).

> This is a **research platform, not a trading platform.** The engine simulates fills against historical bars purely to evaluate hypotheses. There is no live trading, no broker integration, no real-time position management.

The product is the *loop*: `hypothesis → code → backtest → verdict → next hypothesis`. The reference example bundled in this repo is a VXX volatility-range short strategy with treasury hedging, but the platform itself is strategy-agnostic.

---

## Table of contents

- [Architecture](#architecture)
- [Data flow](#data-flow)
- [Strategies: where they live and how they are created](#strategies-where-they-live-and-how-they-are-created)
- [Setup](#setup)
- [Configuration](#configuration)
- [Using the system](#using-the-system)
- [Improving an existing strategy](#improving-an-existing-strategy)
- [Creating a new strategy](#creating-a-new-strategy)
- [Repo layout](#repo-layout)
- [Lint, test, CI](#lint-test-ci)
- [OpenSpec](#openspec)
- [License](#license)

---

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

Two trust boundaries are load-bearing:

- **PyO3 boundary** — the Python orchestrator only calls into trusted Rust crates that we own (gateway, ledger, kb, engine control plane, build pipeline). No LLM-emitted code ever runs in-process.
- **Worker boundary** — every LLM-compiled strategy executes in a separate `engine-worker` subprocess. A panic, OOM, or timeout in a strategy kills the worker, not the orchestrator. Process isolation is the safety boundary; there is no in-process sandboxing.

---

## Data flow

End-to-end, one iteration of the research loop:

```
1. data-gateway      fetch + cache + normalize OHLCV bars
2. engine            run baseline BatchSpec against the current strategy
3. ledger            record run + BacktestResult (append-only)
4. hypothesis-loop   diagnose → kb_query → generate → critique → rank → select
                     ├─ KB hybrid retrieval (Kuzu graph + LanceDB vector)
                     ├─ reasoning model (Anthropic / OpenAI)
                     └─ persist accepted/rejected decisions to the ledger
5. tester            translate hypothesis → parameter diff OR new Rust source
                     ├─ build-pipeline: lint, allowed-crate check, cargo build
                     ├─ smoke backtest on a small slice
                     └─ full BatchSpec to the engine (walk-forward + stress)
6. engine + ledger   record verdict, link back to the hypothesis id
7. (loop)            verdict feeds the next diagnose pass
```

Reproducibility invariant: every run pins the strategy-artifact hash, dataset-manifest hash, parameters, modes, seed, and runner version. Identical inputs produce byte-identical `BacktestResult`s. The ledger plus the local cache are sufficient to byte-identically reproduce any historical run via `strategy-gpt replay --run-id <id>` (see [`experiment-ledger/spec.md`](./openspec/changes/rewrite-architecture/specs/experiment-ledger/spec.md)).

Detailed per-stage contracts:

| Stage | Crate / module | Spec |
|---|---|---|
| Fetch, cache, consolidate | `crates/data-gateway` | [`data-gateway/spec.md`](./openspec/changes/rewrite-architecture/specs/data-gateway/spec.md) |
| Backtest | `crates/engine` + `engine-rt` | [`backtest-engine/spec.md`](./openspec/changes/rewrite-architecture/specs/backtest-engine/spec.md) |
| Ledger | `crates/ledger` | [`experiment-ledger/spec.md`](./openspec/changes/rewrite-architecture/specs/experiment-ledger/spec.md) |
| Knowledge base | `crates/kb` (v1 SQLite stand-in; Kuzu + LanceDB swap-in is a localized refactor) | [`knowledge-base/spec.md`](./openspec/changes/rewrite-architecture/specs/knowledge-base/spec.md) |
| Hypothesis loop | `python/strategy_gpt/hypothesis_loop.py`, `nodes.py`, `diagnose.py`, `kb_query.py` | [`hypothesis-loop/spec.md`](./openspec/changes/rewrite-architecture/specs/hypothesis-loop/spec.md) |
| Tester | `python/strategy_gpt/tester.py` | [`tester/spec.md`](./openspec/changes/rewrite-architecture/specs/tester/spec.md) |
| Optimizer | `python/strategy_gpt/optimizer.py` | [`param-optimizer/spec.md`](./openspec/changes/rewrite-architecture/specs/param-optimizer/spec.md) |
| Objective specs | `crates/objectives` + per-strategy `objective.yaml` | [`objectives/spec.md`](./openspec/changes/rewrite-architecture/specs/objectives/spec.md) |
| Build pipeline | `crates/build-pipeline` (incl. `whitelist.toml`) | [`strategy-runtime/spec.md`](./openspec/changes/rewrite-architecture/specs/strategy-runtime/spec.md) |

---

## Strategies: where they live and how they are created

Strategies are **Rust `cdylib` crates** that implement the sealed `engine_rt::Strategy` trait. They are loaded by the engine worker at runtime via `libloading`; they do not link statically to the orchestrator.

Two reference strategies are checked in:

- **`crates/vxx-strategy/`** — the canonical reference strategy: short VXX during low realized vol, flat when realized vol crosses a high threshold. Used by the recorded smoke run and as a target for the hypothesis loop. Its objective spec lives at `crates/vxx-strategy/objective.yaml`.
- **`crates/example-strategy/`** — a no-op fixture used by the plugin-loader tests.

### The `Strategy` trait

Every strategy implements five lifecycle methods (see [`strategy-runtime/spec.md`](./openspec/changes/rewrite-architecture/specs/strategy-runtime/spec.md)):

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

### Two ways strategies get authored

1. **Human-authored**, like `crates/vxx-strategy/`. You write `src/lib.rs`, implement `Strategy`, and call the `strategy_entry!(factory)` macro at the bottom of the file. The macro is the *only* `#[no_mangle] extern "C"` surface; you never write `unsafe` yourself.
2. **LLM-authored**, through the build pipeline. The hypothesis loop's Tester (`python/strategy_gpt/tester.py`) hands an LLM a hypothesis plus the strategy-generation prompt. The LLM emits Rust source. The build pipeline lints it (parse check + allowed-crate enforcement against `crates/build-pipeline/whitelist.toml`), then `cargo build`s it with `sccache` and content-addressed caching. Successfully built artifacts are stored keyed by `hash(source)` and reused on subsequent identical builds.

The allowed-crate whitelist is the dependency-surface guard for LLM-emitted code. It is human-curated, versions are *not* pinned, and any direct or dev/build dependency outside the list causes the linter to reject the strategy before `cargo build` is even invoked. See [`crates/build-pipeline/whitelist.toml`](./crates/build-pipeline/whitelist.toml).

Strategy artifacts are versioned against the runner ABI (`RunnerVersion` in `engine-rt`). The runtime carries a **single** ABI version at any time; a major version bump triggers re-generation of every artifact from source through the LLM. There is no multi-version compatibility shim.

---

## Setup

### Prerequisites

- Rust toolchain pinned to `1.82.0` (`rust-toolchain.toml`).
- Python `>=3.11`.
- Optional but recommended: `sccache` for incremental Rust builds.
- Optional: `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY` if you intend to drive the hypothesis loop with a real reasoning model. The recorded smoke run and all offline tests use stubs and run without credentials.

### One-time install

```bash
# 1. Rust workspace
cd crates && cargo check --workspace

# 2. Python orchestrator + native PyO3 extension
cd ../python && pip install -e '.[dev]'
pip install maturin
maturin develop -m ../crates/py-bindings/Cargo.toml

# 3. Build the reference strategy + worker so end-to-end paths can load them
cd ../crates && cargo build -p vxx-strategy -p example-strategy --bin engine-worker

# 4. Pre-commit hooks (enforces the same lint suite as CI)
cd .. && pre-commit install
```

`make lint` and `make test` (from the repo root) are the canonical entry points after setup.

---

## Configuration

### Environment variables

| Variable | Purpose | Required |
|---|---|---|
| `ANTHROPIC_API_KEY` | Reasoning calls in `diagnose`/`critique` (and Tester source emission) | Only for live hypothesis-loop runs |
| `OPENAI_API_KEY` | Alternative reasoning provider | Only for live hypothesis-loop runs |
| `RUSTC_WRAPPER=sccache` | Incremental compile cache for the build pipeline | Recommended |

Smoke runs, replays, and the test suite work without any API keys.

### Per-strategy objective spec (`objective.yaml`)

Every strategy declares its own `objective.yaml` next to its `Cargo.toml`. This single file drives **both** the evaluator and the parameter optimizer (see [`objectives/spec.md`](./openspec/changes/rewrite-architecture/specs/objectives/spec.md)). The VXX reference spec at `crates/vxx-strategy/objective.yaml` is a complete worked example:

```yaml
primary:
  metric: sharpe
  target: ">= 1.0"
  weight: 1.0

secondary:
  - metric: max_drawdown
    target: "<= 0.20"
    mode: constraint          # hard-fail any candidate that violates this
    weight: 1.0
  - metric: profit_factor
    target: ">= 1.2"
    mode: soft                # contributes to score per `tradeoff`
    weight: 0.5

tradeoff: lexicographic       # or `weighted_sum`, or `pareto`

walk_forward:
  folds: 8
  gap: 1
  oos_min_score: 0.5          # OOS gate — sub-threshold candidates are rejected
```

Validation rules: every named metric must be one the engine emits, every constraint must be a valid comparison, weights must be non-negative, and `pareto` requires at least two contributing metrics. Spec validation fails fast — before any backtest runs.

### Strategy parameters

Parameters are typed knobs the strategy exposes through `__params__` state. They are mutable without recompilation. The optimizer sweeps over them; the hypothesis loop can propose changing them via parameter-diff hypotheses (no rebuild) or replacing logic via source-change hypotheses (rebuild required).

Example: in `crates/vxx-strategy/src/lib.rs`, `VxxParams { vol_lo, vol_hi, size, symbol }` is serde-deserialized from `ctx.state_get("__params__")` in `on_init`.

### Allowed-crate whitelist

Edit `crates/build-pipeline/whitelist.toml` to grant LLM-emitted strategies access to a new crate. Removal is breaking — any existing artifact that depends on the removed crate must be regenerated.

### Cache and ledger roots

The CLI defaults to `./cache` (data gateway) and `./ledger` (experiment ledger), both gitignored. Override per command with `--root` / `--ledger-root` / `--gateway-root`.

---

## Using the system

The Python package installs a `strategy-gpt` console script powered by Typer. The CLI source is [`python/strategy_gpt/cli.py`](./python/strategy_gpt/cli.py).

```bash
strategy-gpt --help

strategy-gpt version
strategy-gpt fetch              # pull a dataset through the data gateway
strategy-gpt cache-stats        # blob count + total bytes in the cache
strategy-gpt recent-decisions   # dump the ledger's recent-decisions view
strategy-gpt replay             # reconstruct a recorded run from the ledger
strategy-gpt run                # submit a BatchSpec to the engine
strategy-gpt ingest             # KB ingestion (phase-8 subcommand)
strategy-gpt hypothesize        # hypothesis-loop entry
strategy-gpt optimize           # parameter optimizer entry
```

### End-to-end smoke run

The recorded smoke run is the canonical "is everything wired" check. It exercises every public surface — gateway, engine, ledger, KB, hypothesis loop, tester, optimizer — with stubbed reasoning so it has no API-key dependency. Its output (`SmokeReport`) is a regression fixture; subsequent commits must not silently change its shape or content.

```bash
cd python && python -c "from strategy_gpt.smoke import run_smoke; print(run_smoke())"
```

### Fetch a dataset

```bash
strategy-gpt fetch \
  --provider yfinance --symbol VXX \
  --start 2020-01-01 --end 2024-12-31 \
  --resolution day --adjustment back-adjusted \
  --mode prefer_cache
```

Cache modes: `prefer_cache` (default, reuse on hit), `validate` (refetch and diff against cache), `force_refresh` (bypass cache), `offline` (no network). Bars are normalized to UTC and cached as year-segmented content-addressed parquet blobs.

### Run a backtest batch

```bash
strategy-gpt run \
  --spec batch.json \
  --artifact crates/target/debug/libvxx_strategy.dylib \
  --worker  crates/target/debug/engine-worker \
  --bars    bars.json \
  --time-cap-secs 60 \
  --mem-cap-bytes 1073741824
```

The engine compiles the strategy at most once per batch and runs each `RunSpec` in its own worker subprocess with the supplied resource caps. Result handles are returned so callers can poll.

### Replay a recorded run

```bash
strategy-gpt replay --run-id <ledger-run-id>
```

Realizes "reproducibility from ledger alone": the ledger + local cache are sufficient to rebuild the `BatchSpec`, fetch the same bars, and produce a byte-identical `BacktestResult`.

### Inspect decisions

```bash
strategy-gpt recent-decisions --limit 50
```

Decisions are accepted/rejected hypotheses with rationale, citations, and timestamps. They are loaded back as context on the next hypothesis-loop run so the loop does not re-propose ideas it has already rejected.

---

## Improving an existing strategy

The platform is built to *iterate* on a strategy. The two improvement loops:

### 1. Parameter optimization (no rebuild)

The optimizer reads the strategy's `objective.yaml` and sweeps parameters across walk-forward folds. Each candidate is evaluated by the engine; constraint-violating candidates are hard-rejected; surviving candidates are scored per the tradeoff mode (`lexicographic`, `weighted_sum`, or `pareto`).

```bash
strategy-gpt optimize    # (driver wiring lives in python/strategy_gpt/optimizer.py)
```

Three search methods: `grid`, `random`, `bayesian` (TPE). All deterministic given the same seed. The output is the optimized parameter set, its walk-forward aggregated metrics, **and** a natural-language rationale that consults both the optimizer's observed surface and the KB (`python/strategy_gpt/rationale.py`).

### 2. Hypothesis loop (parameter or logic change)

```bash
strategy-gpt hypothesize
```

The loop is a LangGraph workflow with explicit, observable nodes: `diagnose → kb_query → generate → critique → rank → select`. Internally `generate → critique → rank` iterates until one of three termination conditions hits: K hypotheses accepted, iteration budget exhausted, or candidate-to-prior-rejected similarity saturation. Termination reason is always recorded.

Each accepted hypothesis ships to the Tester with:

- A human-readable name.
- The metric it intends to improve.
- A **falsification criterion** — the threshold or sign that would falsify it.
- The proposed change (parameter diff *or* new strategy source intent).
- KB citations (book/page, paper/section).
- An estimated lift confidence.

The Tester decides parameter-diff vs source-change automatically: parameter diffs reuse the existing artifact; source changes go through `build-pipeline` (lint → allowed-crate check → cargo build → smoke backtest on a small slice → full BatchSpec). Any failure short-circuits to `rejected: build_failed | smoke_failed | falsification_not_met`, with the failure artifact (compiler diagnostic, panic message, metric movement) captured in the ledger.

Why the loop gets better over time: every accepted *and* rejected decision is persisted with its rationale. On the next run, `bootstrap_state_from_ledger` (in `python/strategy_gpt/hypothesis_loop.py`) seeds the loop with prior decisions, so `critique` can read prior rejection rationale before re-evaluating a similar candidate.

---

## Creating a new strategy

You have two paths.

### Path A — Author it yourself in Rust

1. Add a new `crates/<name>-strategy/` package with `crate-type = ["cdylib"]` and a dependency on `engine-rt`.
2. Implement `engine_rt::Strategy` for your type. Use `Context` for *all* I/O — never reach for `std::fs`, `reqwest`, or threads.
3. Call `engine_rt::strategy_entry!(factory)` at the bottom of `src/lib.rs`, where `factory` returns a `Box<dyn Strategy>`. The macro emits the C-ABI registration symbol.
4. Add `<name>-strategy/objective.yaml` declaring primary metric, constraints, tradeoff mode, and walk-forward config.
5. `cargo build -p <name>-strategy` and point `strategy-gpt run --artifact ...` at the resulting `.so` / `.dylib`.

`crates/vxx-strategy/src/lib.rs` is the worked reference — copy it as a template.

### Path B — Generate it from a hypothesis

Drive the hypothesis loop against an empty-or-baseline strategy plus an objective spec. The loop's `generate` node, paired with the Tester's LLM source emitter and the build pipeline, produces a complete strategy crate. The resulting artifact is content-addressed and registered in the ledger like any other.

Either way, the **allowed-crate whitelist applies to LLM-emitted strategies**. Human-authored strategies under `crates/` are not gated by the source linter (they are trusted code in the workspace), but anything coming out of the build pipeline must conform.

---

## Repo layout

```
crates/                 Rust workspace
  engine-rt/            Strategy trait, Context capability handle, RunnerVersion, strategy_entry! macro
  engine/               BatchSpec, coordinator, worker, modes (Plain / MonteCarlo / Slippage / RegimeFilter / Sensitivity)
  data-gateway/         providers, year-segmented content-addressed cache, normalizer, consolidator
  ledger/               SQLite append-only + parquet sidecars
  kb/                   hybrid retrieval (Kuzu graph + LanceDB vector; v1 ships a SQLite stand-in matching the same contract)
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
  optimizer.py          Grid / random / TPE search over walk-forward folds
  objectives.py         Pydantic mirror of the YAML objective spec
  rationale.py          LLM-grounded rationale generator (consults KB)
  reasoning.py          Reasoning-model client (Anthropic / OpenAI)
  types.py              Cross-FFI types mirroring the Rust serde records
  smoke.py              End-to-end recorded smoke run + SmokeReport fixture

kb/                     Curated source list, starter corpus, recorded fixtures
cache/                  Year-segmented content-addressed parquet (gitignored)
ledger/                 SQLite ledger + parquet sidecars (gitignored)
openspec/               Change proposals and capability specs
```

---

## Lint, test, CI

Single source of truth: `make lint` (same suite locally and in CI).

```bash
pre-commit install   # one-time
make lint            # rustfmt --check + clippy + ruff check + ruff format --check + mypy --strict
make fmt             # write fixes (rustfmt + ruff format)
make test            # cargo test --workspace + pytest
```

Stance:

- **Rust** — tool defaults. No `.rustfmt.toml` or `clippy.toml`. `cargo fmt --all -- --check` and `cargo clippy --workspace --all-targets -- -D warnings`.
- **Python** — strict. Ruff with a wide rule set (`E,F,W,I,B,UP,SIM,RUF,S,N,PT,ANN,C4,ERA,PL`) plus `ruff format --check` plus `mypy --strict`. Mypy strict scope is `python/strategy_gpt/`; tests and `kb/` are excluded explicitly.

Tool versions are pinned in `.pre-commit-config.yaml`. Pre-commit scopes to staged files; `make lint` covers the whole tree. CI runs the same `make lint` + `make test` plus a smoke-fixture byte-identity check; see `.github/workflows/ci.yml`.
