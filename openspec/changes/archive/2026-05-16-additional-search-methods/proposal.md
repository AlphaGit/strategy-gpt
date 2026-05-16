# Additional search methods — CMA-ES, DE, Sobol, Successive Halving, LHS+polish

## Why

The base optimizer ships with `grid`, `random`, `bayesian` (TPE), and `recursive_grid`. Online research and the black-box optimization literature (BBOB benchmarks, Hansen et al.) consistently identify several methods that outperform random and TPE on the kinds of noisy, low-dimensional, multi-modal surfaces produced by trading-strategy backtests. Adding them broadens the platform's coverage of practical research workflows without changing the optimizer's external contract (per-fold train search, cross-fold OOS validation, packed-batch dispatch, parquet persistence).

Five methods are added by this change, chosen because each fills a distinct niche the existing four do not cover:

- **CMA-ES** — adapts to elongated ridges in the parameter surface (e.g. `stop_loss × lookback` interactions). Strong on smooth-but-noisy continuous surfaces; parallelizes per generation.
- **Differential Evolution** — handles mixed-integer / multi-modal surfaces where CMA-ES struggles; same parallelism profile.
- **Sobol quasi-random** — drop-in `random` replacement with better space-filling; ideal as the seeding phase for evolutionary methods.
- **Successive Halving** — multi-fidelity over a *fold-count* axis (evaluate candidates on 2 folds, kill bottom half, double folds, repeat). Reduces wasted compute on bad candidates; full Hyperband intentionally deferred (the bracket sweep wastes evaluations on aggressive-early-stopping brackets, which are particularly poor for finance where early-period signal is weak).
- **LHS + local polish** — defensible small-budget baseline using Latin Hypercube global coverage + Hooke-Jeeves polish from top-k LHS points. Hooke-Jeeves preferred over Nelder-Mead because Nelder-Mead is fragile on noisy objectives.

Out of scope:
- **PSO** rejected (finance papers favor it; BBOB shows CMA-ES dominates).
- **Simulated annealing** rejected (sequential, cooling-schedule tuning is its own optimization problem).
- **Nelder-Mead** rejected as primary; only acceptable as a polish sub-method behind a feature flag.
- **Full Hyperband** rejected; Successive Halving is the useful subset.
- **PBT** rejected (designed for online RL schedules; wrong semantics for fixed-window backtests).

## What Changes

- **MODIFIED capability** `param-optimizer`:
  - Extend the method enum: `cma_es`, `differential_evolution`, `sobol`, `successive_halving`, `lhs_polish`.
  - Add a method-specific knob block per new method.
  - Add a scenario for each new method (one happy path each).
  - Strengthen the determinism requirement: every new method MUST use seeded RNG state recorded in the optimization manifest.
  - Update the `--benchmark` predictor requirement: planned-run-count formulas MUST cover the new methods.
- **NO changes** to the experiment-spec schema beyond the enum values inside `optimize.method` and the new sub-blocks under `optimize.<method>`.
- **NO changes** to the cross-fold OOS validation flow; new methods plug into the same per-fold orchestrator.

## Capabilities

### Modified Capabilities

- `param-optimizer`: extended method enum, new method-specific knob blocks, new scenarios, predictor extensions.

## Impact

- **Code**:
  - `python/strategy_gpt/optimizer.py` — add `CmaEsSearcher`, `DESearcher`, `SobolSearcher`, `SuccessiveHalvingSearcher`, `LhsPolishSearcher`.
  - `python/strategy_gpt/optimization_runner.py` — wire Successive Halving's fold-count fidelity axis through the per-fold orchestrator (override: instead of evaluating every candidate on every fold, evaluate the rung's surviving candidates on `rung_folds` folds).
  - `python/strategy_gpt/benchmark.py` — add plan-run formulas for the new methods.
  - `python/strategy_gpt/experiment_spec.py` — extend the `OptimizeBlock` pydantic union with per-method knob models.
- **Dependencies**:
  - `cma` (pip) for CMA-ES reference implementation (Hansen et al.; well-maintained).
  - `scipy.optimize.differential_evolution` for DE OR a small in-house impl if the scipy version proves non-deterministic across versions; prefer scipy if it pins.
  - `scipy.stats.qmc.Sobol` for Sobol sequences.
  - Hooke-Jeeves: small in-house impl (~80 lines; no good pinned third-party option).
- **Tests**:
  - Each method on a 2-D synthetic objective with known optimum.
  - Determinism: same seed → identical candidate sequence per method.
  - Successive Halving: candidates whose early-fold scores are bottom-half MUST NOT appear in the final rung's `trials.parquet` rows for later rungs.
- **Out of scope (follow-ups)**:
  - Multi-fidelity over OOS time-window length (instead of fold count).
  - GP-based Bayesian optimization (separate from TPE).
  - Combinations like CMA-ES warm-started from Sobol (mention in design.md, defer to a future change).
