## 1. Dependencies

- [x] 1.1 Add `cma` to Python dependencies, pinned (target `cma==3.3.0` or latest stable ≥ 7 days old per supply-chain rule).
- [x] 1.2 Confirm `scipy >= 1.11` is in dependency floor; bump if needed (verify pinned version ≥ 7 days old).
- [x] 1.3 Document the version pin policy and the supply-chain rule reference in `docs/optimization.md`.

## 2. CMA-ES searcher

- [x] 2.1 Add `CmaEsSearcher` to `python/strategy_gpt/optimizer.py` wrapping `cma.CMAEvolutionStrategy`.
- [x] 2.2 Implement `popsize: auto` → `4 + floor(3 * ln(D))`.
- [x] 2.3 Implement bounds modes `clip` (default) and `reject` (redraw).
- [x] 2.4 Mixed-integer handling: round + de-duplicate per generation; warn + inflate sigma on > 30% duplicate rate.
- [/] 2.5 Restart strategies: `null`, `ipop`, `bipop` (delegate to `cma`).
- [x] 2.6 Pydantic model `CmaEsKnobs` in `experiment_spec.py`.
- [x] 2.7 Per-generation packed batch dispatch through the per-fold orchestrator.

## 3. Differential Evolution searcher

- [x] 3.1 Add `DESearcher` wrapping `scipy.optimize.differential_evolution`.
- [x] 3.2 Implement `popsize: auto` → `15 * D`.
- [x] 3.3 Default `init: sobol` (use the project's Sobol searcher for the seed phase).
- [x] 3.4 Default `integrality=True` on every declared `IntParam`.
- [x] 3.5 Pydantic model `DEKnobs`.
- [x] 3.6 Per-generation packed batch dispatch.

## 4. Sobol searcher

- [x] 4.1 Add `SobolSearcher` wrapping `scipy.stats.qmc.Sobol` with Owen scrambling.
- [x] 4.2 Enforce `n_points` is a power of 2; round up with a warning if not.
- [x] 4.3 Pydantic model `SobolKnobs`.
- [x] 4.4 Single packed batch per fold.

## 5. Successive Halving searcher

- [ ] 5.1 Add `SuccessiveHalvingSearcher` in-house implementation.
- [ ] 5.2 Initial candidates generated via `SobolSearcher` (default) or `LHS` (knob).
- [ ] 5.3 Extend the per-fold orchestrator: this method bypasses the "every candidate × every fold" assumption — orchestrator MUST consult the method to learn each candidate's fold subset.
- [ ] 5.4 Extend the `trials.parquet` `phase` enum with `train_fold_<i>_rung_<r>`; document in the persistence layout.
- [ ] 5.5 Pydantic model `SuccessiveHalvingKnobs`.
- [ ] 5.6 Test: 64 candidates, eta=3, 8 folds → survivor counts `64 → 21 → 7 → 2` and matching parquet row counts.

## 6. LHS + Hooke-Jeeves searcher

- [ ] 6.1 Add `LhsPolishSearcher`.
- [ ] 6.2 Implement Hooke-Jeeves in-house (~80 lines) following the axis-aligned pattern-search rules.
- [ ] 6.3 Parallelize the `top_k_polish` polish trajectories at the engine-batch level (each polish step's `2D` axis-probes packed as a single sub-batch).
- [ ] 6.4 Nelder-Mead polish behind `--experimental-polish-nelder-mead` flag; document fragility on noisy objectives.
- [ ] 6.5 Pydantic model `LhsPolishKnobs`.

## 7. Benchmark predictor extensions

- [/] 7.1 Add per-method plan-run-count formulas in `python/strategy_gpt/benchmark.py`.
- [ ] 7.2 Successive Halving requires summing per-rung costs; predictor MUST account for the surviving-candidate cascade.
- [ ] 7.3 Update the `--benchmark` printed report to display the method-specific breakdown (e.g., "rung 0: 64 cands × 2 folds = 128 runs").

## 8. Method-versus-method advisory

- [ ] 8.1 In the `--benchmark` printout, when a search space has more integer than float dims, append a one-line note suggesting `differential_evolution` over `cma_es`.
- [ ] 8.2 When `sobol` is selected with `n_points < 2^(D+3)`, append a warning that coverage may be too sparse for D dimensions.

## 9. Determinism manifest

- [ ] 9.1 The optimization manifest records: method name, knob block (canonicalized), library name + version (e.g., `cma==3.3.0`, `scipy==1.11.4`), seed, and the resolved `popsize` / `n_generations` values for `auto` knobs.
- [ ] 9.2 Replay verification test: re-run a stored optimization manifest and assert byte-identical candidate sequence per method.

## 10. Tests

- [ ] 10.1 Each method on a 2-D synthetic objective with a known global optimum: assert convergence within tolerance, assert seed-determinism, assert `trials.parquet` row count matches the predictor formula.
- [x] 10.2 CMA-ES on a 4-D mixed (2 float, 2 int) space: assert int params are integer in every recorded trial.
- [x] 10.3 Sobol with `n_points=128, scramble=true, owen_seed=42`: assert byte-identical sequence across two runs.
- [x] 10.4 DE with `init=sobol`: assert the first-generation candidates match a standalone Sobol run with the same seed and `n=popsize`.
- [ ] 10.5 Successive Halving end-to-end: assert killed candidates do not appear in later-rung trials; assert the final-rung survivors are the cross-OOS evaluated candidates.
- [ ] 10.6 LHS + Hooke-Jeeves: assert Hooke-Jeeves halves step_size when no improvement found in a sweep; assert convergence on a paraboloid.

## 11. Docs

- [ ] 11.1 Add a `docs/optimization.md` section "Search methods" with one paragraph per method, when to pick it, citation, and a knob-block reference.
- [ ] 11.2 Update `docs/cli-cookbook.md` with one optimize-recipe per new method.
