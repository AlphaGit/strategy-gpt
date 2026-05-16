# Parameter optimization

Strategy-GPT's optimizer drives the engine through a declarative
experiment-spec: a per-fold search over the strategy's parameter space,
followed by cross-fold OOS validation, followed by an
**overfitting-aware selection layer** that gates and re-ranks the final
candidate before `best.json` is published.

See `docs/experiment-spec.md` for the spec schema and `docs/cli-cookbook.md`
for recipes.

## Selection layer

The selection layer sits *above* the search method and operates on the
search's output: `trials.parquet` (every backtest the optimizer
commissioned) and `manifest.json` (the experiment-spec, objective, fold
ranges, seed, and methodology citations). It is a pure function of
those inputs plus the selection knobs — running it twice over the same
artifacts produces byte-identical output, which is what makes
`strategy-gpt optimize reselect` reproducible.

The layer has three independent computations applied to the top-K
candidates from the cross-fold OOS validation phase: Probability of
Backtest Overfitting (PBO), Deflated Sharpe Ratio (DSR), and a
parameter-sensitivity (robust) score. The first is a *gate* — if PBO
exceeds the threshold the whole run is marked `rejected_pbo` and no
`best` is published without `--force`. The other two are
*re-rankings* applied to the surviving top-K.

### Probability of Backtest Overfitting (PBO)

Combinatorially Symmetric Cross-Validation (CSCV) constructs every
equal-half split of the per-fold OOS metric matrix and counts how often
the in-sample-best candidate lands in the *bottom* half of the
out-of-sample ranking. PBO is that fraction. When PBO exceeds the
threshold (default 0.5), the IS-best is systematically *worse* than
chance on OOS — the canonical overfitting signal.

- For ≤ 16 folds: enumerate every `C(S, S/2)` split.
- For > 16 folds: Monte Carlo sample `max_splits` splits; the seed is
  recorded in the manifest for replay.

Citation: Bailey, Borwein, López de Prado, Zhu (2017), *The Probability
of Backtest Overfitting*, J. Computational Finance.

### Deflated Sharpe Ratio (DSR)

The maximum Sharpe observed across many trials is upward-biased by
multiple testing. DSR adjusts for the expected maximum Sharpe under the
null and reports the probability that the *true* Sharpe exceeds zero:

- `E[max SR | N]` uses the Bailey/López de Prado approximation with
  Euler-Mascheroni mixing.
- `Var(SR)` accounts for non-normality via skew and excess kurtosis
  (defaults: normal-returns assumption since the engine's aggregate
  metrics dict does not carry per-trade moments).
- `Effective N` is the number of *distinct* parameter sets evaluated
  (default), or the raw `trial_count`.

By default the selection layer ranks the surviving top-K by DSR
descending; ties break by raw primary score, then by lower per-fold OOS
variance.

Citation: Bailey & López de Prado (2014), *The Deflated Sharpe Ratio*,
J. Portfolio Management.

### Parameter-sensitivity (robust) score

For each top-K candidate, compute the mean − λ·std of the objective
score over the *k* nearest already-evaluated trials in min-max-normalized
parameter space (Euclidean over numeric dims, 0/1 distance per
categorical mismatch). The candidate's own score participates in the
neighborhood mean. The neighborhood draws from the *full* trial
history, not just the top-K — this leverages the search's own
exploration.

The robust score is *always reported* in `best.json`. By default it is
not used for ranking. Pass `--robust-objective` (or set
`optimize.robust_objective: true` in the spec) to make the final ranking
use robust score instead of DSR. The robust score is computed only at
selection time — the per-fold search always sees the raw objective so
its convergence dynamics are not muddled.

Citations: López de Prado (2018), *Advances in Financial Machine
Learning*, Wiley (ch. 11–12); Pardo (2008), *The Evaluation and
Optimization of Trading Strategies*, Wiley (ch. 9).

## Decision logic

```python
if PBO > pbo_threshold and not force:
    decision = rejected_pbo
elif robust_objective:
    rank top-K by robust_score desc
else:
    rank top-K by DSR desc (tie: raw_score, then lower per-fold OOS variance)
final = top-1 of the ranked list (if any survive constraints)
```

`best.json` always records the full top-K scores plus the decision,
even when the decision is `rejected_pbo`. The candidate the configured
ranking would have picked in the absence of the gate is recorded as
`would_have_picked` for transparency.

## Knobs (experiment-spec)

```yaml
optimize:
  method: recursive_grid
  ...
  robust_objective: false           # opt-in for robust-score ranking
  selection:
    pbo:
      enabled: true
      threshold: 0.5
      top_k: 50
      max_splits: 4096              # cap for sampled splits when S > 16
    deflated_sharpe:
      enabled: true
      top_k: 50
      effective_n: distinct_params  # | trial_count
    sensitivity:
      enabled: true
      neighborhood_k: 8
      penalty: 1.0                  # lambda
```

All three sub-blocks default to `enabled: true`. Disable individual
layers for debugging or methodological isolation.

## CLI

- `strategy-gpt optimize --spec experiment.yaml` — run the search; the
  selection layer always runs and writes `decision`, `pbo`,
  `deflated_sharpe`, `sensitivity_score`, and `selection_methodology`
  into `best.json`.
- `--robust-objective` — final-rank by robust score instead of DSR.
- `--pbo-threshold T` — override the default 0.5 threshold; `T ∈ [0, 1]`.
- `--force` — proceed despite a `rejected_pbo` decision; both the
  original PBO and the override are recorded.
- `strategy-gpt optimize reselect <opt_id> [flags...]` — re-run the
  selection layer over an existing optimization's artifacts; writes
  `best_<timestamp>.json` next to the original without overwriting it.
- `strategy-gpt optimize compare <opt_id> <best_a> <best_b>` — print a
  side-by-side diff of two selection outputs from the same `opt_id`.

## When selection rejects a run

A `rejected_pbo` result is a research signal, not a failure. The system
is telling you the IS-best of your top-K does not survive
out-of-sample — your search either over-explored a noisy parameter
space or your folds are too coupled. Options:

1. Inspect `would_have_picked` and the per-trial PBO inputs in
   `best.json`; if the per-fold OOS metrics show one candidate
   dominating most folds, raise the threshold via
   `optimize reselect --pbo-threshold` and document the override.
2. Add folds or a larger train slice so each candidate's per-fold
   metric has lower sampling variance.
3. Re-rank by robust score (`--robust-objective`) to favor stability
   over peak — this is often what survives in practice.
4. Use `--force` only when you have an out-of-band reason to publish
   the candidate (a known data anomaly, a planned A/B). The override is
   permanent in the manifest.
