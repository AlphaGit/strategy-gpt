# `BatchSpec` JSON reference

!!! warning "Internal: engine input across the PyO3 boundary; not user-authored"
    Operators describe experiments via [`experiment-spec`](experiment-spec.md); the loader translates that envelope into a `BatchSpec` before submit. This document is here for engine engineers and replay tooling.

The engine accepts a single input: a **`BatchSpec`**. One strategy, one dataset, N runs. Every CLI invocation of `strategy-gpt run --spec <file>` and every Python call into `Engine.submit_batch(...)` resolves to a `BatchSpec` matching the schema below.

The canonical Rust source is `crates/engine/src/spec.rs`; the capability contract is `openspec/specs/backtest-engine/spec.md`. This document is the operator-facing description of the on-disk JSON shape.

---

## Concepts at a glance

Strategy-GPT is a research loop, not a trading platform. Every backtest reduces to "run this strategy against these historical bars, with these knobs, and tell me how it performed." The schema below names the moving parts.

### `BatchSpec` — the unit of work the engine accepts

A `BatchSpec` is **one strategy + one dataset + N independent runs**. It's the *only* input shape the engine ever takes. Whether you want a single backtest, a 200-point parameter sweep, or a Monte-Carlo bootstrap of stress scenarios, it all comes in as a `BatchSpec`.

Why batch them? Because the strategy is compiled **once per batch** and reused across every run. Compilation dominates wall-clock time for many runs of the same strategy; batching amortizes it. The engine fans the runs out to a worker subprocess pool so they execute in parallel, subject to `parallelism`.

A `BatchSpec` is the level at which the ledger records lineage. Every accepted hypothesis, every optimizer iteration, every smoke test eventually becomes a `BatchSpec` whose hash, dataset reference, and verdict are written to the ledger.

### `RunSpec` — one backtest configuration

A `RunSpec` is the smallest re-executable unit: **one parameter set, one set of execution modes, one time slice, one seed**. Each run produces one `BacktestResult`.

Multiple `RunSpec`s in the same `BatchSpec` are how you express things like:

- A parameter sweep — same strategy, different `params` per run, same slice. The **parameter optimizer** (`python/strategy_gpt/optimizer.py`) emits these for you when you optimize against an `objective.yaml`.
- A walk-forward evaluation — same params, different `slice` per fold. Folds are declared in the strategy's objective spec.
- A stress matrix — same params and slice, different `modes` (e.g. Monte-Carlo on one run, slippage grid on another).

### `Mode` — what kind of run this is

Modes turn a single run into a family of runs the engine knows how to aggregate. They live on the `RunSpec` so the strategy itself never has to know whether it's executing in plain, stress, or sensitivity context — the engine reshuffles the bars or sweeps the parameter on its own.

- **`plain`** — straight backtest over the slice. Default.
- **`monte_carlo`** — block-bootstrap resamples of the input bars; produces confidence intervals on the metrics.
- **`slippage`** — re-execute with a grid of slippage values to see how cost-sensitive the strategy is.
- **`regime_filter`** — restrict execution to specific historical windows (e.g. only 2020 Q1 + 2022 Q4) to test regime fragility.
- **`sensitivity`** — sweep a single parameter across values; produces a metric surface keyed by the swept dimension.

Only `plain` is wired in the executor; the other variants are reserved in the schema so optimizer/tester code can emit them ahead of executor support landing.

### `EngineConfig` — how trades are simulated

This is the **simulation environment**, not the strategy. It applies uniformly to every run in the batch. It says when a submitted order intent gets a price (`fill_model`), how much starting equity the run has (`initial_capital`), what costs to apply (`commission_per_fill`, `slippage_bps`), and what ceilings catch obviously-broken strategies (`sanity`).

Importantly: this is a **backtest harness**. There is no broker, no real-time tick feed, no live position. "Fill" means "the engine picks a price off the next bar and updates the simulated position book." Sanity bounds are *backtest-validity* checks (they catch a strategy that submits 1000× equity in size), not live risk controls. Trips don't halt the run — they appear in `exec_log` so the diagnostic record is preserved.

### `TimeRange` and `slice` — which bars the strategy sees

A half-open `[start, end)` UTC window. Bars outside the slice are dropped before the strategy's `on_bar` is called. The slice is the obvious dial for walk-forward evaluation: each fold is a different slice over the same dataset.

### `params` — strategy knobs

Opaque JSON object. The engine doesn't inspect it; it goes into `state["__params__"]` and the strategy's `on_init` deserializes it. Two `RunSpec`s differing only in `params` are how you sweep a parameter without recompiling — same artifact hash, different inputs.

The shape of `params` is defined by the strategy, not the engine. For the VXX reference strategy: `{"vol_lo": <float>, "vol_hi": <float>, "size": <float>, "symbol": <string>}`. For your strategy, whatever your `Strategy::on_init` chooses to read.

### `seed` — determinism anchor

`(strategy artifact, dataset, params, modes, slice, seed, runner version)` is the full reproducibility key. Identical key ⇒ byte-identical `BacktestResult`. Change any of these and you get a new run record on the ledger.

### `strategy` / `dataset` — ledger labels

Both are opaque strings. The engine doesn't load anything from them; the actual compiled artifact arrives via `--artifact <path>` and the bars via `--bars <path>`. These strings exist so the ledger has a stable identifier to show on `recent-decisions` and `replay`. Convention: use the strategy artifact's content hash and the gateway's manifest hash so the labels also serve as deduplication keys.

### `parallelism` — soft fan-out cap

Hint to the worker coordinator: don't run more than N worker subprocesses concurrently. Useful when you have a 200-point sweep on a laptop with 8 cores. Local single-process executors ignore this.

---

## Top-level shape

```json
{
  "strategy":     "<StrategyArtifactRef>",
  "dataset":      "<DatasetRef>",
  "runs":         [ <RunSpec>, ... ],
  "engine":       <EngineConfig>,
  "parallelism":  <usize>
}
```

| Field         | Type                | Description |
|---------------|---------------------|-------------|
| `strategy`    | string              | Opaque identifier for the compiled strategy artifact. In practice this is the build-pipeline's `ArtifactKey::as_hex()` (content-addressed hash of the strategy source). The engine treats it as an opaque label that ends up on the ledger record for traceability. |
| `dataset`     | string              | Opaque dataset identifier. Typically the gateway's manifest hash (so the run can later be replayed from the ledger). |
| `runs`        | array of `RunSpec`  | One backtest configuration per element. The strategy is compiled at most **once per batch** and reused across every `RunSpec`. |
| `engine`      | `EngineConfig`      | Per-batch execution settings (fill model, initial capital, costs, sanity bounds). Applies uniformly to every run. |
| `parallelism` | non-negative int    | Soft limit on concurrent worker subprocesses. The single-process executor used by tests ignores it; the multi-worker coordinator respects it. Set to `1` for deterministic local runs. |

> **Note:** the `strategy` and `dataset` strings on a `BatchSpec` are *labels* for the ledger, not file paths. The engine receives the actual compiled artifact bytes via `strategy-gpt run --artifact <path>` and the bars via `--bars <path>`. The strings here are what shows up on `recent-decisions` and `replay` output.

---

## `RunSpec`

A single backtest configuration: one parameter set, zero or more modes, one slice, one seed.

```json
{
  "params": { ... },
  "modes":  [ <Mode>, ... ],
  "seed":   <u64>,
  "slice":  { "start": "<UTC ISO 8601>", "end": "<UTC ISO 8601>" }
}
```

| Field    | Type           | Description |
|----------|----------------|-------------|
| `params` | object (free-shape JSON) | Strategy parameters. Opaque to the engine — passed through to the strategy's `on_init` via `state["__params__"]`. For the VXX reference strategy: `{"vol_lo": 0.01, "vol_hi": 0.04, "size": 100.0, "symbol": "VXX"}`. |
| `modes`  | array of `Mode`| Execution modes. **Only `Plain` is implemented in the executor.** The other variants are reserved here so `RunSpec` is shape-stable and stress/sensitivity batches can be authored ahead of executor support. |
| `seed`   | `u64`          | Deterministic seed. Same `(strategy artifact, dataset, params, modes, slice, seed, runner version)` produces a byte-identical `BacktestResult`. |
| `slice`  | `TimeRange`    | Half-open `[start, end)` UTC window. Bars outside the slice are dropped before the strategy sees them. |

### `TimeRange`

```json
{ "start": "2018-01-01T00:00:00Z", "end": "2026-12-31T00:00:00Z" }
```

UTC, ISO 8601, half-open. Bars with `start ≤ ts < end` are included.

### `Mode`

Serde tag: `kind` (snake_case).

| `kind`            | Extra fields                                                       | Description |
|-------------------|--------------------------------------------------------------------|-------------|
| `plain`           | —                                                                  | Straight backtest over the slice. The only variant the executor runs. |
| `monte_carlo`     | `n: u32`, `block_size: u32`                                        | Block-bootstrap resamples of the input bars. Aggregated metrics with confidence intervals land in `BacktestResult.stress`. |
| `slippage`        | `bps_grid: number[]`                                               | Apply each slippage value (in bps) to every fill; one sub-result per grid point. |
| `regime_filter`   | `ranges: TimeRange[]`                                              | Restrict execution to the listed historical ranges. |
| `sensitivity`     | `param: string`, `values: number[]`                                | Sweep one parameter across `values`; output keyed by parameter value lands in `BacktestResult.sensitivity`. |

Examples:

```json
{ "kind": "plain" }
{ "kind": "monte_carlo", "n": 1000, "block_size": 20 }
{ "kind": "slippage",    "bps_grid": [1.0, 5.0, 10.0] }
{ "kind": "regime_filter", "ranges": [
    { "start": "2020-03-01T00:00:00Z", "end": "2020-05-01T00:00:00Z" }
]}
{ "kind": "sensitivity", "param": "vol_lo", "values": [0.005, 0.01, 0.015, 0.02] }
```

---

## `EngineConfig`

Per-batch execution settings.

```json
{
  "fill_model":          "NextBarOpen",
  "initial_capital":     100000.0,
  "commission_per_fill": 0.0,
  "slippage_bps":        0.0,
  "sanity":              { "max_intent_size": 1.0e9, "max_position_size": 1.0e9 }
}
```

| Field                 | Type                                | Default       | Description |
|-----------------------|-------------------------------------|---------------|-------------|
| `fill_model`          | `"NextBarOpen"` \| `"CurrentBarClose"` | `NextBarOpen` | When an `on_bar` order intent fills. `NextBarOpen` is realistic for daily bars; `CurrentBarClose` matches end-of-day decisions that print at the close. |
| `initial_capital`     | float                               | `100000.0`    | Starting equity. Used by the equity curve and drawdown calculations. |
| `commission_per_fill` | float                               | `0.0`         | Flat commission charged per fill, in price-times-size currency units. |
| `slippage_bps`        | float                               | `0.0`         | Fixed-fraction slippage applied to every fill. `5.0` = 5 bps = 0.0005. |
| `sanity`              | `SanityBounds`                      | see below     | Backtest-validity ceilings — **not** live risk controls. Trip = `exec_log` entry; the run keeps going. |

### `SanityBounds`

```json
{ "max_intent_size": 1.0e9, "max_position_size": 1.0e9 }
```

| Field               | Type  | Description |
|---------------------|-------|-------------|
| `max_intent_size`   | float | Maximum absolute size of any single submitted intent (instrument units, e.g. shares / contracts). |
| `max_position_size` | float | Maximum total absolute position size per symbol. |

These exist to catch degenerate hypotheses (e.g. a strategy that submits 1000× equity in size on every bar). They are not live-trading risk controls. Trips appear in `BacktestResult.exec_log` so the diagnostic record is preserved.

---

## Full worked example

This is the file at `examples/vxx/batch.json` — a single-run backtest of the VXX reference strategy across the full 2018-2026 cached history.

```json
{
  "strategy": "vxx-local",
  "dataset":  "vxx-local-demo",
  "runs": [
    {
      "params": {
        "vol_lo": 0.01,
        "vol_hi": 0.04,
        "size":   100.0,
        "symbol": "VXX"
      },
      "modes": [{ "kind": "plain" }],
      "seed":  42,
      "slice": {
        "start": "2018-01-01T00:00:00Z",
        "end":   "2026-12-31T00:00:00Z"
      }
    }
  ],
  "engine": {
    "fill_model":          "NextBarOpen",
    "initial_capital":     100000.0,
    "commission_per_fill": 0.0,
    "slippage_bps":        0.0,
    "sanity": { "max_intent_size": 1.0e9, "max_position_size": 1.0e9 }
  },
  "parallelism": 1
}
```

Submit it:

```bash
strategy-gpt run \
  --spec examples/vxx/batch.json \
  --artifact crates/target/debug/libvxx_strategy.dylib \
  --worker crates/target/debug/engine-worker \
  --bars examples/vxx/bars.json \
  --dataset-manifest <gateway-manifest-hash>
```

---

## Multi-run example: parameter sweep

200 runs of the same strategy with different `vol_lo` values, single compile, parallel execution.

```json
{
  "strategy": "vxx-local",
  "dataset":  "vxx-local-demo",
  "runs": [
    { "params": { "vol_lo": 0.005, "vol_hi": 0.04, "size": 100.0, "symbol": "VXX" },
      "modes": [{ "kind": "plain" }], "seed": 1,
      "slice": { "start": "2018-01-01T00:00:00Z", "end": "2026-12-31T00:00:00Z" } },
    { "params": { "vol_lo": 0.010, "vol_hi": 0.04, "size": 100.0, "symbol": "VXX" },
      "modes": [{ "kind": "plain" }], "seed": 2,
      "slice": { "start": "2018-01-01T00:00:00Z", "end": "2026-12-31T00:00:00Z" } }
    /* ... 198 more ... */
  ],
  "engine": { /* ... as above ... */ },
  "parallelism": 8
}
```

The strategy is compiled exactly once; 200 backtests run across up to 8 worker subprocesses.

In practice you do not hand-author this — the **parameter optimizer** (`python/strategy_gpt/optimizer.py`) walks a search space (`grid` / `random` / `bayesian`) and emits the `BatchSpec` for you, applying the strategy's `objective.yaml` to score candidates over walk-forward folds. See `openspec/specs/param-optimizer/spec.md`.

---

## Reproducibility invariants

A `BatchSpec` + a dataset + a runner version uniquely determines the output. The ledger records all of:

- strategy artifact hash (content-addressed)
- dataset manifest hash
- `params`, `modes`, `seed`, `slice` per run
- `EngineConfig` (fill model, costs, sanity)
- runner ABI version

Identical inputs produce a byte-identical `BacktestResult`. `strategy-gpt replay --run-id <id>` reconstructs the `BatchSpec` + bars from the ledger + local cache and re-runs.

---

## Output: `BacktestResult` (per run)

Not part of the `BatchSpec`, but worth knowing what comes back. Each run produces a `BacktestResult` containing:

- `metrics` — Sharpe, Sortino, Profit Factor, Win Ratio, Max Drawdown, Annualized Return, trade-length stats.
- `trades` — closed simulated trades with entry/exit timestamps, side, size, pnl, reasons, active signals at entry.
- `signals` — every signal evaluation (including `fired=false` with `suppressed_by`).
- `equity` — per-bar equity, drawdown, exposure.
- `regimes` — post-hoc regime annotations.
- `exec_log` — decision events (blocked entries, filter suppressions, sanity-bound trips).
- `meta` — artifact hash, dataset manifest hash, seed, runner version.
- `stress` / `sensitivity` — present when the corresponding modes ran.

Full schema and scenarios: `openspec/specs/backtest-engine/spec.md`.
