# Parameter optimization

## Search methods

The optimizer ships several search methods, selectable per run via
`optimize.method`. Each owns a sibling `optimize.<method>` knob block;
unknown keys are rejected at spec-validation time. The optimization
manifest always records the resolved knob values plus the library name +
version used so a run can be replayed bit-for-bit later.

### `sobol`

Owen-scrambled quasi-random sequence (Owen 1995; Sobol 1967). Better
space-fill than `random` at the same budget — a near drop-in replacement
that meaningfully improves coverage in 2-8 dimensions. Typical use:
strong random baseline, or as the seed phase for evolutionary methods
(see `differential_evolution.init: sobol`).

```yaml
optimize:
  method: sobol
  sobol:
    n_points: 256       # power-of-two; non-powers are rounded up + warned
    scramble: true
    owen_seed: 42
```

Library: `scipy.stats.qmc.Sobol`. Determinism: fully seedable when
scrambled; deterministic by construction otherwise.

### `successive_halving`

Multi-fidelity over the fold-count axis (Jamieson & Talwalkar 2016).
Evaluates `initial_candidates` Sobol-seeded points on `initial_folds`
folds, drops the bottom 1 - 1/eta by mean score, doubles the fold
budget, repeats until the full fold count is reached. Final-rung
survivors are cross-validated like every other method's winners.

Trades early-cascade compute for steeper-than-random pruning of bad
candidates. Most cost goes into the small-fold rungs; the full-fold
evaluation is paid only by the survivor handful.

Categorical params are not supported when `init_method: sobol`;
declare them as ints with a numeric encoding instead.

```yaml
optimize:
  method: successive_halving
  successive_halving:
    initial_candidates: 64
    eta: 3
    initial_folds: 2
    init_method: sobol
    init_seed: 42
```

Library: in-house driver over the project's Sobol/Random seeders.
Phase tags emitted as `train_fold_<i>_rung_<r>` so the parquet log
makes the cascade recoverable: candidates killed at rung r exist only
in their own folds' rung-r-and-earlier rows. Full Hyperband (bracket
sweeps) is intentionally out of scope.

### `cma_es`

Covariance Matrix Adaptation Evolution Strategy (Hansen 2016). Adapts
to elongated ridges in the parameter surface (think `stop_loss x
lookback` interactions). Population-based, parallelizes per generation.
The optimizer rescales the space to the unit cube before driving
`cma.CMAEvolutionStrategy`, so `sigma0` is a fraction of the per-dim
range. Integer params are rounded + de-duplicated per generation;
sustained > 30% duplicate rates emit a warning and inflate sigma for
that fold. Categorical params are not supported (use ints with a
numeric encoding).

```yaml
optimize:
  method: cma_es
  cma_es:
    popsize: auto                       # auto -> 4 + floor(3 * ln(D))
    sigma0: 0.3
    n_generations: 50
    restart_strategy: null              # ipop / bipop land later
    bounds: clip                        # | reject
```

Library: `cma`. Determinism: cma honors `seed=`; record in manifest.
`restart_strategy: ipop|bipop` not yet wired — only `null` runs today.

### `differential_evolution`

Storn & Price (1997) differential evolution via
`scipy.optimize.differential_evolution` in `vectorized=True` mode —
every generation packs the full population into a single engine batch.
Strong on noisy, multi-modal surfaces with mixed-integer parameters
(integer dims sweep through the solver's `integrality` flag). Sobol
init by default, matching :class:`SobolSearcher`'s first `popsize`
points byte-for-byte. Choice (categorical) params are not supported —
declare them as ints with a numeric encoding.

```yaml
optimize:
  method: differential_evolution
  differential_evolution:
    popsize: auto                       # auto -> 15 * D
    n_generations: 50
    strategy: best1bin                  # | rand1bin | currenttobest1bin
    mutation_low: 0.5
    mutation_high: 1.0
    crossover: 0.7
    init: sobol                         # | latinhypercube | random
```

Library: `scipy.optimize.differential_evolution`. Determinism: scipy
honors `seed=`; population init is Sobol-seeded for byte-equivalent
first generation across replays.

### Other methods

`recursive_grid` (default), `grid`, `random`, `bayesian` (TPE). See
existing `optimize.<method>` knob blocks in
[`docs/experiment-spec.md`](experiment-spec.md). `lhs_polish` lands in the next chunk.

## Supply-chain rule

All direct dependencies pinned per the project's supply-chain freshness
rule: every version installed MUST be ≥ 7 days old at install time. The
manifest records the resolved version per method (e.g.,
`scipy==1.17.1`); a future scipy / cma release that breaks determinism
falls back to the previous compliant pin in the manifest, not the
in-house module.



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
