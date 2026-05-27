# Running an optimization

## Learning goal

Sweep a strategy's parameters across cross-validation folds, read the resulting `best.json`, and compare two selection outputs from the same trial set.

## Prerequisites

- You have completed [Your first backtest](first-backtest.md). The VXX strategy artifact, the engine worker, and the cache root all exist and work.
- `jq` on `$PATH` (for pretty-printing the optimization outputs).
- A few minutes of CPU time. The default recursive grid runs ~200 backtests against four folds.

## Walkthrough

### 1. Write an optimization experiment-spec

Save the following as `vxx-opt.yaml` in the repo root:

```yaml
artifact: crates/target/debug/libvxx_strategy.dylib
strategy_label: vxx-opt-tutorial

bars:
  request:
    provider: yfinance
    symbol: VXX
    start: 2018-01-01T00:00:00Z
    end:   2024-12-31T00:00:00Z
    resolution: Day
    adjustment: back_adjusted

engine:
  fill_model: NextBarOpen
  initial_capital: 100000.0

runs:
  - params:
      size:   100.0
      symbol: VXX
    modes:
      - { kind: plain }
    seed: 42
    slice:
      start: 2018-01-01T00:00:00Z
      end:   2024-12-31T00:00:00Z

folds:
  count: 4
  scheme: rolling

optimize:
  method: recursive_grid
  seed: 42
  aggregator: mean
  space:
    vol_lo:
      type: float
      low:  0.001
      high: 0.05
    vol_hi:
      type: float
      low:  0.01
      high: 0.20
  recursive_grid:
    resolution: 5
    top_k: 1
    depth: 3
    plateau_epsilon: 0.0001
  persist:
    root: ./ledger
    name: vxx-tutorial

parallelism: auto
caps:
  time_cap_secs: 120
```

Save the following as `objective.yaml` next to it:

```yaml
primary:
  metric: sharpe
  target: ">= 0.5"
  weight: 1.0

secondary:
  - metric: max_drawdown
    target: "<= 0.30"
    mode: constraint
    weight: 1.0

tradeoff: lexicographic

folds:
  count: 4
  scheme: rolling
  gap: 0
  oos_min_score: 0.0
```

No stdout output (this step writes files). `runs[].params` fixes everything *except* the two parameters under `optimize.space`; the optimizer sweeps `vol_lo` and `vol_hi`, treating `size` and `symbol` as constants. The 4-fold rolling split is much lighter than the production 8-fold spec at `crates/vxx-strategy/objective.yaml` — that's intentional for a tutorial.

### 2. Run the optimization

```bash
strategy-gpt optimize --spec vxx-opt.yaml
```

Expected (truncated):

```
opt_id: 30073677e8d12291
folds: 4
parallelism: 7
trials: 216 (rejected 0)
best:
  params: {"vol_hi": 0.01, "vol_lo": 0.001}
  aggregate_metrics: {"annualized_return": 0.0, "avg_trade_length_bars": 0.0, "max_drawdown": 0.0, "n_trades": 0, "profit_factor": 0.0, "sharpe": 0.0, "sortino": 0.0, "win_ratio": 0.0}
  score: 0.000000
  fold winners: [{"vol_hi": 0.01, "vol_lo": 0.001}, {...}, {...}, {...}]
ledger: ledger/optimizations/30073677e8d12291
```

Exact numbers depend on your bars window and the local toolchain; the *shape* is the invariant. The "rejected" count is trials the objective constraints rejected (e.g. max_drawdown > 0.30). `fold winners` lists the best parameter set per fold's *train* slice; the published `best` is the candidate whose OOS aggregate across all folds ranks highest under the overfitting-aware selection layer. As with [Your first backtest](first-backtest.md), the metrics here are zero because `realized_vol_20` does not warm up enough within the fold slices for this short window — the *shape* of the optimization output is the invariant under test, not the numerical values.

### 3. Inspect `manifest.json` and `best.json`

The optimization ledger is now under `ledger/optimizations/<opt_id>/`. Pretty-print the manifest:

```bash
strategy-gpt optimize inspect 30073677e8d12291 --json | jq '.manifest | {status, method, folds_count: (.folds | length), parallelism: .resolved_parallelism, trial_count}'
```

Expected:

```json
{
  "status": "completed",
  "method": "recursive_grid",
  "folds_count": 4,
  "parallelism": 7,
  "trial_count": 216
}
```

And the selection decision in `best.json`:

```bash
jq '{decision, pbo, final: {params: .final.params, aggregate_score: .final.aggregate_score}}' \
  ledger/optimizations/30073677e8d12291/best.json
```

Expected:

```json
{
  "decision": {
    "status": "accepted",
    "best_trial_id": 0,
    "would_have_picked": 0,
    "reason": "PBO=0.0000 <= threshold=0.5000",
    "robust_objective": false,
    "pbo_threshold": 0.5,
    "effective_n": 73,
    "history_size": 200,
    "ranking": [0, 1, 2, 3],
    "force_override": false
  },
  "pbo": {
    "value": 0.0,
    "n_splits": 6,
    "enumerated": true,
    "n_trials": 4,
    "n_folds": 4,
    "seed": null,
    "threshold": 0.5,
    "rejected": false
  },
  "final": {
    "params": {"vol_hi": 0.01, "vol_lo": 0.001},
    "aggregate_score": 0.0
  }
}
```

`decision.status` is `accepted` when PBO falls below the threshold (default 0.5) and the primary objective is met. The same payload also carries `deflated_sharpe[]`, `sensitivity_score[]`, and a `selection_methodology` block recording which knobs produced this decision.

### 4. Re-select with a robust-objective override

The selection layer can re-rank the existing trial set without re-running any backtests. Re-rank by parameter-sensitivity (robust) score:

```bash
strategy-gpt optimize reselect 30073677e8d12291 --robust-objective
```

Expected:

```
ledger/optimizations/30073677e8d12291/best_20260521T214804Z.json
```

The original `best.json` is preserved; a sibling `best_<timestamp>.json` records the new selection.

### 5. Compare the two selection outputs

```bash
strategy-gpt optimize compare 30073677e8d12291 best.json best_20260521T214804Z.json
```

Expected:

```
comparing best.json vs best_20260521T214804Z.json
  ≠ decision:
      a: {"best_trial_id": 0, "robust_objective": false, "status": "accepted", ...}
      b: {"best_trial_id": 0, "robust_objective": true,  "status": "accepted", ...}
  = pbo:
      a: {"value": 0.0, "n_splits": 6, ...}
      b: {"value": 0.0, "n_splits": 6, ...}
  = would_have_picked:
      a: 0
      b: 0
  final.params (a): {"vol_hi": 0.01, "vol_lo": 0.001}
  final.params (b): {"vol_hi": 0.01, "vol_lo": 0.001}
```

`≠` flags the `decision` block (the `robust_objective` field differs). `=` on `pbo` confirms PBO is a property of the trial set, not the ranking; switching the ranking does not perturb it. On a richer trial set the `final.params` row often diverges between the two rankings, surfacing the operator question the cookbook's [Interpret a PBO rejection](../how-to/interpret-pbo-rejection.md) page is for.

## What you just did

You declared a fold-aware parameter search in the experiment-spec, ran ~200 backtests across four rolling folds via `strategy-gpt optimize`, and let the overfitting-aware selection layer pick the published winner. You then re-ranked the same trial set under a different objective (robust score in place of Deflated Sharpe) and diffed the two decisions. Every trial row is recorded in the optimization ledger; you could byte-identically replay any one of them with `strategy-gpt optimize replay <opt_id> --trial <id>`.

## What next

- **Walkthrough** — [Guided CLI walkthrough → Stage 5 (Optimize)](../guided-cli-walkthrough.md#stage-5-optimize): every supported method (`grid`, `random`, `sobol`, `cma_es`, `differential_evolution`, `successive_halving`, `lhs_polish`) and its tuning knobs.
- **How-to** — [Interpret a PBO rejection](../how-to/interpret-pbo-rejection.md): operator actions when `decision.status == "rejected_pbo"`.
- **Reference** — [`experiment-spec` → Optimize](../reference/experiment-spec.md): the full `optimize` and `folds` block schemas.
- **Reference** — [Objective spec](../reference/objective-spec.md): primary, secondary, and tradeoff knobs.
- **Explanation** — [Overfitting & selection](../explanation/overfitting-and-selection.md): why PBO, Deflated Sharpe, and sensitivity scores are the three legs of the selection layer.
- **Decision** — [ADR 0011 — PBO threshold default 0.5](../decisions/0011-pbo-threshold-default-0_5.md): the rationale behind the default you just inherited.
