# Design — additional search methods

## 1. CMA-ES

Wrap `cma.CMAEvolutionStrategy` (Hansen 2016). Per-fold integration: each generation samples `popsize` candidates from the current `N(m, σ²·C)`, the runner packs them all as one batch for the active fold's train slice, scores are returned to CMA-ES, which updates `m`, `σ`, and `C`. After convergence, the fold winner is the best-of-history candidate (CMA-ES already tracks this).

Knob block:

```yaml
optimize:
  method: cma_es
  cma_es:
    popsize: auto                       # auto → 4 + floor(3 * ln(D))
    sigma0: 0.3                         # initial step, fraction of param range
    n_generations: 50
    restart_strategy: ipop              # null | ipop | bipop
    bounds: clip                        # clip | reject (reject = redraw)
```

Determinism: `cma` accepts a seed via `'seed'` option; record in manifest.

Mixed-integer caveat: CMA-ES samples reals; integer params handled by rounding + de-duplication. If too many duplicates within a generation (>30%), runner logs a warning and inflates `sigma0` for that fold. If a strategy has more integer than float dims, recommend DE instead in `--benchmark` output.

Run-count for predictor: `popsize × n_generations` per fold; `folds × that` total + `folds²` for the cross-OOS phase.

## 2. Differential Evolution

Wrap `scipy.optimize.differential_evolution` if `scipy >= 1.11` pins the RNG state cleanly; otherwise small in-house impl following Storn & Price (1997). Algorithm choice: `best1bin` default, configurable. Population evaluated per generation as one packed batch.

Knob block:

```yaml
optimize:
  method: differential_evolution
  differential_evolution:
    popsize: auto                       # auto → 15 × D
    n_generations: 50
    strategy: best1bin                  # best1bin | rand1bin | currenttobest1bin
    mutation: [0.5, 1.0]                # F sampled per generation
    crossover: 0.7                      # CR
    init: sobol                         # sobol | latinhypercube | random
```

Determinism: scipy honors `seed=`; record in manifest. Use `sobol` init by default — better coverage than the scipy default.

Mixed-integer: scipy DE supports integer constraints via `integrality=` (scipy ≥ 1.9). Default ON for any int params declared in the search space.

## 3. Sobol quasi-random

Wrap `scipy.stats.qmc.Sobol`. Owen-scrambled (`scramble=True`). Single-pass: generate `n_points` Sobol points up front, evaluate as one packed batch per fold.

Knob block:

```yaml
optimize:
  method: sobol
  sobol:
    n_points: 256                       # power-of-2 enforced
    scramble: true
    owen_seed: 42                       # only when scramble=true
```

Determinism: scrambled Sobol with fixed `owen_seed` is byte-deterministic. Unscrambled is also deterministic but provides no variance estimate across repeated runs.

Use case: rarely a winner standalone; intended as a seed for CMA-ES / DE (see "Future combinations" below) and as a stronger random baseline.

## 4. Successive Halving (NOT full Hyperband)

Fidelity axis = **number of folds**, not OOS-window-fraction. Rationale: early-period of a single backtest is a weak signal in finance; running on fewer folds preserves the regime-mix signal. Procedure (Jamieson & Talwalkar 2016):

```
rungs = ceil(log_eta(F_total))                          # F_total = folds in experiment
budget_0 = max(2, F_total // eta^(rungs-1))            # initial folds per candidate
candidates_0 = initial_population

for r in 0..rungs:
    # Pack: every surviving candidate × budget_r folds → 1 engine batch
    evaluate each candidate on budget_r folds (rolling fold scheme order)
    rank by OOS-aggregate score on those budget_r folds
    keep top 1/eta survivors
    budget_{r+1} = min(F_total, budget_r * eta)
```

Final rung: top survivors evaluated on the full F-fold cross-OOS like every other method.

Knob block:

```yaml
optimize:
  method: successive_halving
  successive_halving:
    initial_candidates: 64
    eta: 3                              # halving factor; 3 → keep top 1/3
    initial_folds: 2                    # minimum folds for rung 0
```

Determinism: same `initial_candidates` (generated from a seeded Sobol or LHS sequence) + same scoring → same survivors at every rung. Record sequence seed in manifest.

Critical constraint: candidates that are killed at rung r have parquet rows only for the folds they actually ran on. The `phase` enum extends with `train_fold_<i>_rung_<r>` to keep this legible.

## 5. LHS + Hooke-Jeeves polish

Procedure:

```
1. Generate `lhs_n` Latin Hypercube samples (McKay et al. 1979).
2. Evaluate all `lhs_n` on the fold's train slice in one packed batch.
3. Select top `top_k_polish` points by score.
4. Run a Hooke-Jeeves polish from each top-k point in parallel
   (each polish is sequential; k polishes run concurrently).
5. Fold winner = best across LHS evaluations + all polish trajectories.
```

Hooke-Jeeves (axis-aligned pattern search): exploratory moves along each dim by `step_size[i]`, accept improving moves; if no improvement in a full sweep, halve `step_size`; stop when all `step_size[i] < step_min[i]`. ~80 lines.

Knob block:

```yaml
optimize:
  method: lhs_polish
  lhs_polish:
    lhs_n: 128
    top_k_polish: 4
    polish: hooke_jeeves                # hooke_jeeves | nelder_mead (gated by feature flag)
    initial_step: 0.1                   # fraction of dim range
    step_min: 0.001                     # fraction of dim range
    max_polish_iters: 50
```

Determinism: LHS seeded; Hooke-Jeeves is deterministic given start point and step schedule.

Nelder-Mead variant: gated behind `--experimental-polish-nelder-mead`. Documented as fragile on noisy objectives; intentionally hard to enable.

## 6. Benchmark predictor extensions

Plan-run formulas per method (per-fold counts; multiply by `folds_count` then add `folds_count²` for cross-OOS):

| Method | Per-fold run count |
|---|---|
| `cma_es` | `popsize × n_generations` |
| `differential_evolution` | `popsize × n_generations` |
| `sobol` | `n_points` |
| `successive_halving` | `Σ_r (candidates_r × budget_r)` |
| `lhs_polish` | `lhs_n + Σ polish trajectories (bounded by top_k_polish × max_polish_iters × 2D)` |

Successive Halving's special structure means the prediction MUST sum *per-rung* costs; the existing per-fold multiplier does not apply — see § 4.

## 7. Future combinations (not in this change)

- Sobol-warm-started CMA-ES: pre-evaluate `n0` Sobol points, initialize CMA-ES `m` at the best Sobol point and `C` from the top-k Sobol covariance. Often beats plain CMA-ES by ~20% in BBOB-style runs.
- Sobol-warm-started TPE: similar; replaces TPE's startup-trials phase.
- Multi-fidelity over OOS window length (not just fold count).

Tracked as future work; not specified here.

## 8. Library determinism

Method-by-method risk:

| Method | Library | Determinism notes |
|---|---|---|
| CMA-ES | `cma` | Fully seedable; pin `cma==3.3.0`. |
| DE | `scipy.optimize.differential_evolution` | Seedable; pin `scipy >= 1.11`. If a future scipy release breaks determinism, fall back to the in-house impl. |
| Sobol | `scipy.stats.qmc.Sobol` | Owen-scrambled with `seed=` is deterministic. |
| Hooke-Jeeves | in-house | Deterministic by construction. |
| LHS | `scipy.stats.qmc.LatinHypercube` | Seedable. |
| Successive Halving | in-house | Driver only; deterministic given seeded candidate generator. |

Manifest records library + version per method to satisfy "engine version" component of the existing determinism requirement.
