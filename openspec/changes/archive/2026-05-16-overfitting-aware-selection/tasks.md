## 1. CSCV / PBO

- [x] 1.1 Add `python/strategy_gpt/selection/cscv.py` implementing the CSCV split generator (enumerate `binom(S, S/2)` for S ≤ 16, seeded Monte Carlo for S > 16).
- [x] 1.2 Implement the PBO estimator over the `(N, S)` per-fold OOS metric matrix.
- [x] 1.3 Unit test: random-noise objective → PBO close to 0.5; signal-rich synthetic objective → PBO close to 0.
- [x] 1.4 Pydantic model `PboKnobs` (enabled, threshold, top_k, max_splits) in `experiment_spec.py` under `optimize.selection.pbo`.

## 2. Deflated Sharpe Ratio

- [x] 2.1 Add `python/strategy_gpt/selection/dsr.py` implementing the Bailey & López de Prado 2014 formula.
- [x] 2.2 Effective-N resolution: `distinct_params` (default) counts unique parameter combinations; `trial_count` counts every row.
- [x] 2.3 Unit test: hand-computable small example (N=10, known skew/kurt) matches closed-form to within 1e-6.
- [x] 2.4 Pydantic model `DsrKnobs` (enabled, top_k, effective_n) under `optimize.selection.deflated_sharpe`.

## 3. Parameter-sensitivity scoring

- [x] 3.1 Add `python/strategy_gpt/selection/sensitivity.py` computing k-NN neighborhood mean − λ·std over min-max-normalized parameter space.
- [x] 3.2 Categorical params handled via 0/1 distance contribution.
- [x] 3.3 Self-inclusion in the neighborhood mean.
- [x] 3.4 Unit test: knife-edge surface (single point with high score surrounded by low scores) → robust score materially below raw score.
- [x] 3.5 Pydantic model `SensitivityKnobs` (enabled, neighborhood_k, penalty) under `optimize.selection.sensitivity`.

## 4. Selector orchestrator

- [x] 4.1 Add `python/strategy_gpt/selection/selector.py` orchestrating PBO → DSR → sensitivity → final decision per the design's `final_decision` function.
- [x] 4.2 Emit a `SelectionDecision` (status enum: `accepted | rejected_pbo | rejected_constraint`, `best` trial_id, `would_have_picked` trial_id, `reason`, all computed scores per top-K).
- [x] 4.3 Unit test: rejection path on PBO > threshold; override via `force=True` flips to `accepted` with the override recorded.

## 5. Optimizer integration

- [x] 5.1 Wire `selector.run(trials_parquet, manifest)` into the optimization runner immediately before `best.json` is written.
- [x] 5.2 Extend `best.json` schema with `pbo`, `deflated_sharpe` (top-K array), `sensitivity_score` (top-K array), `decision`, `would_have_picked`, `selection_methodology` citations.
- [x] 5.3 Add `optimize.selection.*` blocks to the experiment-spec pydantic union; all blocks default to `enabled: true`.
- [x] 5.4 Add `optimize.robust_objective: bool` (default `false`).

## 6. CLI flags

- [x] 6.1 `strategy-gpt optimize --robust-objective` (overrides spec to `true`).
- [x] 6.2 `strategy-gpt optimize --pbo-threshold T` (overrides default 0.5; T in `[0, 1]`).
- [x] 6.3 `strategy-gpt optimize --force` (proceeds despite `rejected_pbo`; records override in manifest).
- [x] 6.4 `strategy-gpt optimize reselect <opt_id> [flags...]` subcommand: re-runs the selection pipeline against an existing `trials.parquet`; writes `best_<timestamp>.json` (never overwrites).
- [x] 6.5 `strategy-gpt optimize compare <opt_id> <best_file_a> <best_file_b>`: side-by-side diff of two selection outputs from the same `opt_id`.

## 7. Tests

- [x] 7.1 End-to-end: small synthetic optimization → run selection → assert `best.json` has all four new fields populated.
- [x] 7.2 Reselect determinism: `optimize reselect` twice with the same flags → byte-identical output.
- [x] 7.3 Reselect threshold flip: original threshold rejects; reselect with `--pbo-threshold 0.99` accepts → decision changes; both outputs preserved.
- [x] 7.4 `--force` records override but still computes and reports PBO.
- [x] 7.5 `--robust-objective` selects a different candidate than the DSR-ranked top-1 on a deliberately knife-edge synthetic surface.

## 8. Docs

- [x] 8.1 Add `docs/optimization.md` section "Selection layer" with subsections per technique, citations, knob references.
- [x] 8.2 Update `docs/cli-cookbook.md` with recipes for `--robust-objective`, `--pbo-threshold`, `reselect`, `compare`.
- [x] 8.3 Update `CLAUDE.md` Domain vocabulary: add PBO, DSR, robust score, fold winner, OOS aggregate (if not yet added by an earlier change).

## 9. Citations in artifacts

- [x] 9.1 The optimization manifest's `selection_methodology` section MUST include the three primary citations (Bailey/Borwein/López de Prado/Zhu 2017; Bailey & López de Prado 2014; López de Prado 2018 + Pardo 2008).
