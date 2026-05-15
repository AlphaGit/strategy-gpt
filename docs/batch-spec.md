# `BatchSpec` JSON reference

The engine accepts a single input: a **`BatchSpec`**. One strategy, one dataset, N runs. Every CLI invocation of `strategy-gpt run --spec <file>` and every Python call into `Engine.submit_batch(...)` resolves to a `BatchSpec` matching the schema below.

The canonical Rust source is [`crates/engine/src/spec.rs`](../crates/engine/src/spec.rs); the capability contract is [`backtest-engine/spec.md`](../openspec/changes/rewrite-architecture/specs/backtest-engine/spec.md). This document is the operator-facing description of the on-disk JSON shape.

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
| `modes`  | array of `Mode`| Execution modes. **Currently only `Plain` is implemented in the executor.** The other variants are reserved here so `RunSpec` is shape-stable and stress/sensitivity batches can be authored ahead of executor support. |
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
| `plain`           | —                                                                  | Straight backtest over the slice. The only variant the executor currently runs. |
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

This is the file at [`examples/vxx/batch.json`](../examples/vxx/batch.json) — a single-run backtest of the VXX reference strategy across the full 2018-2026 cached history.

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

In practice you do not hand-author this — the **parameter optimizer** (`python/strategy_gpt/optimizer.py`) walks a search space (`grid` / `random` / `bayesian`) and emits the `BatchSpec` for you, applying the strategy's `objective.yaml` to score candidates over walk-forward folds. See [`param-optimizer/spec.md`](../openspec/changes/rewrite-architecture/specs/param-optimizer/spec.md).

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

Full schema and scenarios: [`backtest-engine/spec.md`](../openspec/changes/rewrite-architecture/specs/backtest-engine/spec.md).
