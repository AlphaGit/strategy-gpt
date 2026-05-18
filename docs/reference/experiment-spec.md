# `experiment-spec` reference

A **`experiment-spec`** is the single declarative file consumed by
`strategy-gpt run --spec <file>`. It fully determines a backtest
experiment: which compiled strategy artifact, which bars source, which
engine configuration, which run list, the parallelism cap, and per-run
resource caps. No additional CLI flags or out-of-band inputs are
required to reproduce the run.

The JSON Schema is at
`crates/experiment-spec/schema.json`.
The pydantic loader is `python/strategy_gpt/experiment_spec.py`. The
schema is consumed by the engine indirectly: the loader translates an
`ExperimentSpec` to an internal `BatchSpec` (see [`batch-spec.md`](batch-spec.md))
before submitting to the engine. Callers compose experiments at the
spec level; the engine's PyO3 boundary still accepts `BatchSpec` as
before.

---

## Top-level shape

```yaml
artifact: ../../crates/target/debug/libvxx_strategy.dylib
bars:
  dataset: 29bdecf5fe758d38d524025321aacfb2825daf2fbcce4a3c2c04377bf635b97b
engine:
  fill_model: NextBarOpen
  initial_capital: 100000.0
  commission_per_fill: 0.0
  sanity:
    max_intent_size: 1.0e9
    max_position_size: 1.0e9
runs:
  - params: { vol_lo: 0.35, vol_hi: 0.80, size: 100.0, symbol: VXX }
    modes: [{ kind: plain }]
    seed: 42
    slice:
      start: 2018-01-01T00:00:00Z
      end:   2026-12-31T00:00:00Z
parallelism: 1
caps:
  time_cap_secs: 120
```

JSON is equally accepted (`.json` extension switches the parser).

---

## Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `artifact` | path string | required | Compiled strategy `cdylib`. Relative paths resolve against the spec file's directory. |
| `strategy_label` | string | artifact stem | Opaque label recorded as `BatchSpec.strategy` on the ledger. |
| `bars` | object | required | Polymorphic — see below. |
| `engine.fill_model` | enum | `NextBarOpen` | `NextBarOpen` \| `CurrentBarClose`. |
| `engine.initial_capital` | number | `100000.0` | Starting equity. |
| `engine.commission_per_fill` | number | `0.0` | Per-fill commission in price * size units. |
| `engine.sanity.max_intent_size` | number | `1e9` | Backtest-validity ceiling on submitted intent size. |
| `engine.sanity.max_position_size` | number | `1e9` | Backtest-validity ceiling on simulated position size. |
| `runs[].params` | object | `{}` | Opaque JSON forwarded to the strategy's `on_init`. |
| `runs[].modes` | array | `[{kind: plain}]` | One or more `Mode` entries. |
| `runs[].seed` | integer | `0` | Determinism anchor. |
| `runs[].slice.start` / `runs[].slice.end` | RFC 3339 | required | Half-open `[start, end)` UTC window. |
| `parallelism` | integer \| `auto` | `1` | Worker fan-out cap. `auto` resolves at load time (see below). |
| `caps.time_cap_secs` | number \| null | `null` | Per-run wall-clock cap. |
| `caps.mem_cap_bytes` | integer \| null | `null` | Per-run memory cap (Linux). |
| `folds` | object \| absent | absent | Fold scheme over the slice. Required when `optimize` is set. See "Folds" below. |
| `optimize` | object \| absent | absent | Declarative parameter search over the run template. See "Optimize" below. |

`engine.slippage_bps` is **not accepted** under `engine`. Per-fill
slippage is expressed as a `Slippage { bps_grid }` mode entry on the
affected run(s); the loader rejects specs that include `slippage_bps`
under `engine` with a structured migration error.

### `bars` polymorphism

Exactly one of `dataset` or `request` must be set:

```yaml
# Cache-resident bars — fast path, no provider call.
bars:
  dataset: <manifest_hash>
```

```yaml
# Auto-fetch — runner pulls through the gateway before submitting.
bars:
  request:
    provider: yfinance
    symbol: VXX
    start: 2018-01-01T00:00:00Z
    end:   2026-12-31T00:00:00Z
    resolution: Day
    adjustment: back_adjusted
```

When `request` is provided and the resulting manifest is not yet
cached, the runner fetches via the gateway with `prefer_cache`
semantics, records the resolved manifest hash for the ledger, and then
proceeds to submit the run. When `dataset` is provided and the manifest
is not present in the local cache, the runner exits with a structured
error pointing at `strategy-gpt fetch`.

Setting both `dataset` and `request` is rejected at validation time
before any side effect. Setting neither is also rejected.

### Folds

`folds` declares how the experiment's slice is split into train/OOS
pairs. Both `objectives.folds` and `experiment-spec.folds` share the
same structural fields (`count`, `scheme`, `gap`, `warmup_bars`); the
objective adds `oos_min_score` on top.

```yaml
folds:
  count: 4          # number of train/OOS pairs, >= 2
  scheme: rolling   # rolling | anchored
  gap: 0            # train→OOS gap in segment-units, default 0
  warmup_bars: 50   # optional fixed-bar warmup carved off fold 0's train
```

Derivation rules (see `strategy_gpt.folds.derive_folds`):

- The slice is divided into `2 * count` equal-width segments.
- For fold `i ∈ 0..count`:
  - `rolling`: train = segment `2i`; OOS = segment `2i+1`.
  - `anchored`: train spans `[base.start, end_of_segment 2i+1)`; OOS = segment `2i+1`.
- `gap` shrinks the train window from its tail by `gap` segment-units
  so OOS does not start adjacent to train.
- `warmup_bars` is attached to fold 0 only; the engine subtracts it
  from fold 0's effective train start when honouring it.

### Optimize

`optimize` declares a parameter search over the run template. When
present, `folds` is mandatory. The runner reads the block and dispatches
one fold-aware batch per candidate.

```yaml
optimize:
  method: grid           # recursive_grid | grid | random | bayesian
  seed: 42
  aggregator: mean       # currently the only supported aggregator
  space:                 # per-parameter search shapes
    vol_lo: { type: float,  low: 0.20, high: 0.50, step: 0.05 }
    vol_hi: { type: float,  low: 0.60, high: 1.00, step: 0.10 }
    n_lookback: { type: int, low: 5, high: 60, step: 1 }
    side:    { type: choice, choices: [short, long] }
  grid:           { resolution: 5 }
  random:         { n_samples: 100 }
  bayesian:       { n_init: 8, n_iter: 80 }
  recursive_grid: { levels: 3, shrink: 0.5, resolution: 4 }
  persist:
    root: ./optimize-runs
    name: vxx-grid-baseline
```

Method-specific sub-blocks (`grid`, `random`, `bayesian`,
`recursive_grid`) supply per-method tuning knobs. Only the sub-block
whose name matches `method` is consumed; the loader still parses the
others if present, so a spec author can keep alternates inline.

Validation rules:

- `optimize` without `folds` → `ValueError` naming the missing block.
- Any key in `optimize.space` that is also fixed under `runs[*].params`
  → `ValueError` naming the conflicting keys.
- `optimize.space` references parameters the strategy artifact does
  not declare → `ValueError` from `validate_search_space(...)` at CLI
  load time.

### `parallelism: auto` semantics

`auto` resolves at spec-load time to `max(1, usable_cpu_count - 1)`,
where `usable_cpu_count` is:

- on Linux, `len(os.sched_getaffinity(0))` — honoring cgroup/taskset.
- elsewhere, `os.cpu_count()`.

The resolved integer is what gets recorded into the ledger; the literal
string `auto` is never persisted.

---

## Migration reference: legacy `batch.json` shape

The legacy `batch.json` shape is rejected by the loader with an explicit
error. The field-by-field mapping below is kept as a reference for
anyone porting an old spec file:

| Legacy `batch.json` | New `experiment.yaml` |
|---|---|
| top-level `strategy: <label>` | `strategy_label: <label>` (optional; defaults to artifact stem) |
| top-level `dataset: <label>` | `bars: { dataset: <manifest_hash> }` |
| `runs[*]` | `runs[*]` (same shape: `params`, `modes`, `seed`, `slice`) |
| `engine.fill_model` | `engine.fill_model` |
| `engine.initial_capital` | `engine.initial_capital` |
| `engine.commission_per_fill` | `engine.commission_per_fill` |
| `engine.slippage_bps` | **removed** — express as a `Slippage` mode |
| `engine.sanity` | `engine.sanity` |
| `parallelism` | `parallelism` (now accepts `auto`) |
| CLI `--artifact <path>` | `artifact: <path>` |
| CLI `--bars <bars.json>` | `bars: { dataset: <hash> }` or `bars: { request: ... }` (auto-fetch + materialize) |
| CLI `--dataset-manifest <hash>` | `bars.dataset` |
| CLI `--time-cap-secs <s>` | `caps.time_cap_secs` |
| CLI `--mem-cap-bytes <n>` | `caps.mem_cap_bytes` |
| CLI `--worker <path>` | retained on the CLI (orchestrator infra, not experiment definition) |

The runner refuses to silently coerce legacy files — fix the file
shape, do not paper over it.

### Why `engine.slippage_bps` is gone from the user-facing schema

The Rust `engine::spec::EngineConfig` struct (`crates/engine/src/spec.rs`)
still carries `slippage_bps` — the field is consumed by the `Slippage`
stress mode (`crates/engine/src/modes.rs`) and applied per fill by the
executor (`crates/engine/src/executor.rs`). The split is purely on the
user-facing surface: the experiment-spec loader does not accept
`slippage_bps` under `engine`, and the translation to the internal
`BatchSpec` injects `slippage_bps: 0.0`. Strategies that need slippage
declare it as a `Slippage { bps_grid }` mode on the run.
