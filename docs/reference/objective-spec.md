# Objective spec & selection knobs

The objective spec is per-strategy YAML next to the strategy's `Cargo.toml` (e.g. `crates/vxx-strategy/objective.yaml`). It drives **both** the Evaluator and the parameter Optimizer. This page is the operator-facing schema reference. For the methodology behind the selection-layer knobs, see [Overfitting & selection](../explanation/overfitting-and-selection.md). For ops actions when a run is `rejected_pbo`, see [Interpret PBO rejection](../how-to/interpret-pbo-rejection.md).

## Worked example

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

Every named metric must be one the engine emits. Every constraint must be a valid comparison. Weights must be non-negative. `pareto` requires at least two contributing metrics. Spec validation fails fast — before any backtest runs.

## Top-level fields

| Field | Type | Meaning |
|---|---|---|
| `primary` | object | Single objective driving the search. Required. |
| `secondary` | list[object] | Additional metrics with `mode: constraint` (hard) or `mode: soft` (weighted into score). |
| `tradeoff` | `lexicographic` \| `weighted_sum` \| `pareto` | How the survivors are scored. |
| `walk_forward` | object | Folds, gap, and the OOS-gate threshold. |
| `optimize` | object | Search method, selection layer knobs. See below. |

## Search methods (per-method knob blocks)

The optimizer selects search method via `optimize.method`. Each method owns a sibling `optimize.<method>` knob block; unknown keys are rejected at spec validation. The optimization manifest records the resolved knob values plus the library name + version so a run can be replayed bit-for-bit.

### `sobol`

Owen-scrambled quasi-random sequence (Owen 1995; Sobol 1967). Better space-fill than `random` at the same budget — a near drop-in replacement that meaningfully improves coverage in 2-8 dimensions. Typical use: strong random baseline, or as the seed phase for evolutionary methods (see `differential_evolution.init: sobol`).

```yaml
optimize:
  method: sobol
  sobol:
    n_points: 256       # power-of-two; non-powers are rounded up + warned
    scramble: true
    owen_seed: 42
```

Library: `scipy.stats.qmc.Sobol`. Determinism: fully seedable when scrambled; deterministic by construction otherwise.

### `lhs_polish`

Defensible small-budget baseline. Latin Hypercube ([McKay et al. 1979](../explanation/bibliography.md#mckay-1979)) gives global coverage; Hooke-Jeeves (axis-aligned pattern search) polishes from the top-K LHS points. Each polish step's `2 * D` axis probes pack as one engine batch across all `top_k_polish` trajectories, so per-iteration cost is `top_k_polish * 2 * D` runs. Trajectories deactivate when every dim's step falls below `step_min` (fraction of the dim range); polish-round count in the trial log reveals when each trajectory gave up.

Nelder-Mead polish is gated behind a feature flag — it is fragile on noisy objectives, so the default is the more robust axis-aligned pattern search.

```yaml
optimize:
  method: lhs_polish
  lhs_polish:
    lhs_n: 128
    top_k_polish: 4
    polish: hooke_jeeves
    initial_step: 0.1         # fraction of each dim's range
    step_min: 0.001
    max_polish_iters: 50
    lhs_seed: 42
```

Library: `scipy.stats.qmc.LatinHypercube` (LHS); in-house Hooke-Jeeves (~80 lines). Determinism: LHS seeded; Hooke-Jeeves deterministic by construction.

### `successive_halving`

Multi-fidelity over the fold-count axis ([Jamieson & Talwalkar 2016](../explanation/bibliography.md#jamieson-talwalkar-2016)). Evaluates `initial_candidates` Sobol-seeded points on `initial_folds` folds, drops the bottom \(1 - 1/\eta\) by mean score, doubles the fold budget, repeats until the full fold count is reached. Final-rung survivors are cross-validated like every other method's winners. Trades early-cascade compute for steeper-than-random pruning of bad candidates. Most cost goes into the small-fold rungs; the full-fold evaluation is paid only by the survivor handful.

Categorical params are not supported when `init_method: sobol`; declare them as ints with a numeric encoding instead.

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

Library: in-house driver over the project's Sobol/Random seeders. Phase tags emitted as `train_fold_<i>_rung_<r>` so the parquet log makes the cascade recoverable: candidates killed at rung r exist only in their own folds' rung-r-and-earlier rows. Full Hyperband (bracket sweeps) is intentionally out of scope.

### `cma_es`

Covariance Matrix Adaptation Evolution Strategy ([Hansen 2016](../explanation/bibliography.md#hansen-2016)). Adapts to elongated ridges in the parameter surface (think `stop_loss x lookback` interactions). Population-based, parallelizes per generation. The optimizer rescales the space to the unit cube before driving `cma.CMAEvolutionStrategy`, so `sigma0` is a fraction of the per-dim range. Integer params are rounded + de-duplicated per generation; sustained > 30% duplicate rates emit a warning and inflate sigma for that fold. Categorical params are not supported (use ints with a numeric encoding).

```yaml
optimize:
  method: cma_es
  cma_es:
    popsize: auto                       # auto -> 4 + floor(3 * ln(D))
    sigma0: 0.3
    n_generations: 50
    restart_strategy: null              # only `null` is supported
    bounds: clip                        # | reject
```

Library: `cma`. Determinism: cma honors `seed=`; recorded in the manifest. Only `restart_strategy: null` is wired; IPOP/BIPOP restarts are not supported.

### `differential_evolution`

[Storn & Price (1997)](../explanation/bibliography.md#storn-price-1997) differential evolution via `scipy.optimize.differential_evolution` in `vectorized=True` mode — every generation packs the full population into a single engine batch. Strong on noisy, multi-modal surfaces with mixed-integer parameters (integer dims sweep through the solver's `integrality` flag). Sobol init by default, matching `SobolSearcher`'s first `popsize` points byte-for-byte. Choice (categorical) params are not supported — declare them as ints with a numeric encoding.

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

Library: `scipy.optimize.differential_evolution`. Determinism: scipy honors `seed=`; population init is Sobol-seeded for byte-equivalent first generation across replays.

### Other methods

`recursive_grid` (default), `grid`, `random`, `bayesian` (TPE). See the `optimize.<method>` knob blocks in [experiment-spec.md](experiment-spec.md). Adding a method = a single new file under `python/strategy_gpt/search/` + a one-line registry entry.

### Method/space advisories

The benchmark report's predictor surfaces one-line advisories when the configured method is a poor fit for the declared search space:

- `cma_es` over a space with more integer than float dims → suggest `differential_evolution` (it handles mixed-integer better via scipy's `integrality` constraint).
- `sobol` with `n_points < 2^(D + 8)` → coverage too sparse for a D-dim space; increase `n_points` or pick a different method.

Advisories are also persisted in `BenchmarkReport.advisories` so a recorded benchmark JSON carries them too.

### Determinism manifest

Every optimization run's manifest records:

- `library_versions` — scipy, cma, numpy, pyarrow versions seen at run time, so a replay can detect drift before re-running.
- `resolved_knobs` — concrete values for any `auto` knobs the method resolved at run time (e.g., CMA-ES `popsize = 4 + floor(3 * ln(D))`).

## Selection layer

All three sub-blocks default to `enabled: true`. Disable individual layers for debugging or methodological isolation.

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

| Knob | Default | Meaning |
|---|---|---|
| `optimize.robust_objective` | `false` | If `true`, final ranking uses the robust score instead of DSR. |
| `selection.pbo.threshold` | `0.5` | Run is `rejected_pbo` if PBO > threshold. |
| `selection.pbo.top_k` | `50` | Size of the top-K survivor set the layer operates on. |
| `selection.pbo.max_splits` | `4096` | Monte Carlo split count when fold count exceeds 16. |
| `selection.deflated_sharpe.effective_n` | `distinct_params` | Or `trial_count`. Choose what counts as "a test" in DSR's deflation. |
| `selection.sensitivity.neighborhood_k` | `8` | Number of nearest neighbors in the robust score. |
| `selection.sensitivity.penalty` | `1.0` | \(\lambda\) in `mean − λ·std`. |

## CLI surface

- `strategy-gpt optimize --spec experiment.yaml` — run the search; the selection layer always runs and writes `decision`, `pbo`, `deflated_sharpe`, `sensitivity_score`, and `selection_methodology` into `best.json`.
- `--robust-objective` — final-rank by robust score instead of DSR.
- `--pbo-threshold T` — override the default 0.5 threshold; `T ∈ [0, 1]`.
- `--force` — proceed despite a `rejected_pbo` decision; both the original PBO and the override are recorded.
- `strategy-gpt optimize reselect <opt_id> [flags...]` — re-run the selection layer over an existing optimization's artifacts; writes `best_<timestamp>.json` next to the original without overwriting it.
- `strategy-gpt optimize compare <opt_id> <best_a> <best_b>` — print a side-by-side diff of two selection outputs from the same `opt_id`.

## Supply-chain rule

All direct dependencies pinned per the project's supply-chain freshness rule: every version installed MUST be ≥ 7 days old at install time. The manifest records the resolved version per method (e.g., `scipy==1.17.1`); a scipy / cma release that breaks determinism is pinned back to the previous compliant version in the manifest, not patched in the in-house module.
