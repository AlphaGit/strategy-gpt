# Design — optimize command

## 1. Per-fold search loop

```
for fold in folds(experiment.slice, experiment.folds):
    train_winner = search(
        method = experiment.optimize.method,
        space  = experiment.optimize.space,
        evaluate = lambda params: backtest(params, slice=fold.train),
        score    = objective_score,   # honors constraints + tradeoff
    )
    fold_winners.append(train_winner)

# Cross-validate: every fold winner gets evaluated on every fold's OOS.
cross = {}
for w in fold_winners:
    oos_metrics = [backtest(w.params, slice=f.oos) for f in folds]
    cross[w] = aggregate(oos_metrics, mode = experiment.optimize.aggregator)

final = argmax(cross.values, key = score_oos_aggregate)
```

Final selection rule:
- Per-candidate OOS metrics list across F folds → aggregated by `mean` (only mode v1).
- Aggregate scored by the same `objective_score` pipeline used during search.
- Best aggregate wins. Ties broken by stability (lower variance of per-fold OOS scores).

Engine packing:
- Per round (recursive grid), all candidates × {train slice for current fold} → one `BatchSpec` with `failure_mode: continue`.
- Final cross-validation: F-fold-winners × F OOS slices → one `BatchSpec`.
- All packed batches share the same compiled artifact, so the engine compiles once per optimization run (not per fold).

## 2. Recursive grid algorithm

```
state := { box = full_param_box, depth = 0 }
all_results := []

while depth < max_depth:
    cells := uniform_grid(state.box, resolution_per_dim)          # res^D points
    results := pack_and_run(cells)                                # 1 engine batch
    all_results += results
    best := top_k_cells(results, k = top_k)
    new_box := union_of(best)                                     # k cells → bbox
    if all_dims_converged(state.box → new_box, eps = 1e-4):
        break
    state.box := new_box
    state.depth += 1
```

Defaults:
- `resolution`: 10 per dim (per-dim override via `space.<param>.resolution`)
- `top_k`: 1
- `depth`: 5
- `plateau_epsilon`: 0.0001 (fraction of *original* parameter range)

Convergence test per dimension:
```
shrink_ratio = new_box_width[i] / original_width[i]
dim_converged = shrink_ratio < plateau_epsilon
```
Stop only when *every* dim has `dim_converged == True`.

Cost (D = dim count):
```
runs_per_round = resolution^D
total_runs_per_fold ≈ runs_per_round × depth        (top_k=1)
total_runs ≈ total_runs_per_fold × folds_count
```
For D=2, defaults: 100 × 5 × 8 = 4,000 train runs + 64 cross-OOS runs.
For D=3, defaults: 1,000 × 5 × 8 = 40,000 train runs.

Mitigation for high-D: optional `max_total_runs` cap rejects search start with a configuration error; user picks lower resolution / depth.

Integer params: at deep recursion the cell may collapse to a single int. Freeze that dim; recursion continues on float dims. Choice params: treated as grid axes with full enumeration at each round (no narrowing).

## 3. Plateau-stop semantics

"Full stop when all dimensions converge" → `AND` across dims, not `OR`. Rationale: if vol_lo converges but vol_hi hasn't, the surface in vol_hi may still have meaningful structure. Early-stop only when both have collapsed.

ε = 0.0001 is fraction of *original* range:
- vol_lo range = `[0.1, 0.6]` (width 0.5) → converged at width ≤ `5e-5`.
- That's ~17 doublings of refinement, or ~8 rounds at `res=10, top_k=1`. Hits `depth=5` first for most configurations; plateau-stop is a safety net.

## 4. Benchmark mode

```
strategy-gpt optimize --spec experiment.yaml --benchmark [--sample N]
```

Procedure:
1. Sample N candidates uniformly from `optimize.space` (N=3 default; `--sample` overrides).
2. Run those N × F (folds) backtests as one packed batch with `failure_mode: continue`.
3. Measure: median per-run wall, std, engine spinup (first-result latency minus median).
4. Compute planned-run count from method:
   - `recursive_grid`: `runs_per_round × depth × folds_count + folds_count^2` (cross-validation).
   - `grid`: `prod(resolutions) × folds_count + folds_count^2`.
   - `random` / `bayesian`: `n_iter × folds_count + folds_count^2`.
5. Predict wall: `total_runs × median_per_run / resolved_parallelism`.
6. Predict ledger footprint: `total_runs × 200 bytes` (rough parquet row size).
7. Print a structured report; ask the user to proceed (interactive) or pass `--yes` to skip the prompt.

## 5. Parallelism auto

```
def resolve_parallelism(parallelism: int | str) -> int:
    if parallelism == "auto":
        try:
            cpus = len(os.sched_getaffinity(0))    # honors cgroup
        except AttributeError:
            cpus = os.cpu_count() or 1
        return max(1, cpus - 1)
    return parallelism
```

Resolved once, recorded in the optimization run manifest. Engine `BatchSpec.parallelism` always sees the resolved integer.

## 6. Persistence layout

```
ledger/
  optimizations/
    <opt_id>/                        # opt_id = blake3(experiment-spec-canonical)[:16]
      manifest.json                  # opt-spec hash, artifact hash, dataset hash,
                                     # resolved parallelism, runner version, seed,
                                     # started_at, finished_at, status
      trials.parquet                 # one row per (candidate, fold, phase)
      best.json                      # pointer to winning trial_id + aggregated metrics
      benchmark.json                 # only if --benchmark ran
  optimizations.sqlite               # index: opt_id, name, status, started_at,
                                     # trial_count, parent_strategy_artifact
```

`trials.parquet` schema (one row per backtest the optimizer commissioned):

| column | type | notes |
|---|---|---|
| `trial_id` | uint64 | monotonic within `opt_id` |
| `round` | uint32 | recursive-grid round; 0 for non-rg |
| `phase` | enum | `train_fold_<i>` \| `oos_fold_<i>` \| `final_cross_<i>` |
| `fold_index` | uint32 | which fold this run targets |
| `params` | json (string) | candidate parameter set |
| `seed` | int64 | inherits from optimize.seed + deterministic shift |
| `metrics` | json (string) | engine-returned metrics dict |
| `score` | float64 | objective score (NaN if rejected) |
| `accepted` | bool | post-objective gating |
| `reject_reason` | string | enum-like; empty when accepted |
| `wall_secs` | float64 | per-run wall time |

Footprint: ~200 bytes/row compressed. 1M trials ≈ 200 MB. Per-optimization-run isolation lets us archive old runs without touching SQLite.

Replay-by-trial:
```
strategy-gpt optimize replay <opt_id> --trial <trial_id>
```
- Look up trial row in parquet → recover `params`, `seed`, `fold_index`, `phase`.
- Load manifest → recover artifact, dataset, engine config.
- Reconstruct a single-run BatchSpec, submit to engine, dump full `BacktestResult` JSON.

This means we do *not* store full `BacktestResult` payloads upfront; metrics-only is enough for analytics, and the per-trial replay path covers the rare "this trial deserves a closer look" case.

## 7. Output

Minimal stdout (default):
```
opt_id: 7c4f2e1a3b9d8c52
folds: 8 (rolling, gap=1)
search: recursive_grid res=10 top_k=1 depth=5 plateau_eps=1e-4
parallelism: 11 (auto)
trials: 8024 (rejected 142)
best:
  params: {vol_lo: 0.234, vol_hi: 0.871}
  oos_aggregate: {sharpe: 1.42, max_drawdown: 0.18, profit_factor: 1.61}
  score: 1.42
  fold winners: [...]
ledger: ledger/optimizations/7c4f2e1a3b9d8c52/
```

`--json` flag flips to structured JSON.

## 8. Trade-offs and rejected alternatives

- **OOS-only search vs per-fold search.** OOS-only is simpler and one search. Rejected because it conflates "robust across regimes" with "fits the average." Per-fold makes regime sensitivity legible.
- **TPE as default.** Powerful when backtests are expensive. Rejected because the engine is fast and recursive grid produces a surface users actually look at; TPE produces only a final point.
- **Streaming partial results.** Tempting at high candidate counts. Rejected for v1 — `failure_mode: continue` already gives us at-end recovery, and streaming complicates ledger semantics. Revisit if a single optimization takes > 1 hour.
- **One sqlite row per trial.** Rejected; parquet wins on both footprint and analytic-query speed for the 100K+ regime. SQLite owns the small opt-level index.
- **Storing `BacktestResult` per trial.** Rejected; replay-on-demand is good enough and 100× cheaper.
