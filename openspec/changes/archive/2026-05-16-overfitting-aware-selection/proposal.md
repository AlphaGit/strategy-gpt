# Overfitting-aware selection — CSCV / PBO, Deflated Sharpe, parameter sensitivity

## Why

The base optimizer (per `2026-05-14-optimize-command`) picks the candidate with the best OOS-aggregate score. That selection rule is necessary but not sufficient for trading research: at the scale of thousands to millions of trials per optimization run, the maximum observed Sharpe is upward-biased by multiple testing, and the "best" parameter may sit on a knife-edge peak that does not survive live deployment. The quantitative-finance literature on backtest overfitting (Bailey & López de Prado; Pardo) is consistent that *how you select* matters at least as much as *how you search*.

This change adds an overfitting-aware selection layer that sits above the search method. Independent of which optimizer ran (recursive grid, CMA-ES, Sobol, etc.), this layer:

1. Computes the **Probability of Backtest Overfitting (PBO)** via Combinatorially Symmetric Cross-Validation (CSCV) over the per-fold OOS metric matrix of the top-K candidates. If PBO > a threshold (default 0.5), the whole optimization run is flagged as overfit and no `best` is published without explicit user override.
2. Computes the **Deflated Sharpe Ratio (DSR)** for the primary metric across the effective number of trials, and re-ranks the top-K candidates by DSR before final selection. DSR is reported alongside raw Sharpe in every output surface.
3. Computes a **parameter-sensitivity (robust) score** for the top-K candidates by averaging objective values over a k-NN neighborhood in parameter space and penalizing local-neighborhood std. Optional: when `optimize.robust_objective: true` is set, this robust score replaces the raw objective for *final selection* (but not for in-search ranking — search methods still see the raw objective to avoid muddling their convergence dynamics).

All three are gates / re-rankings *above* the search; none change the per-fold orchestrator or the trial-persistence layout, beyond appending new fields to `best.json` and the optimization manifest.

## What Changes

- **NEW capability** `optimization-selection`: selection layer that runs after the search completes and before `best.json` is written.
- **MODIFIED capability** `param-optimizer`:
  - Integration hook: optimizer MUST invoke the selection layer before publishing `best.json`.
  - `best.json` schema gains `pbo`, `deflated_sharpe`, `sensitivity_score`, `selection_decision` (`accepted | rejected_pbo | rejected_constraint`).
  - New CLI flag `--robust-objective` (also `optimize.robust_objective: true` in spec) re-ranks final by robust score.
  - New CLI flag `--pbo-threshold T` overrides the default rejection threshold (does not unset the flag — rejection is still recorded with the override value).
  - New CLI flag `--force` proceeds despite a PBO-rejection (records the override + reason in manifest).
- The selection layer reads `trials.parquet` and `manifest.json` directly; it can be re-run post-hoc against an already-completed optimization run via `strategy-gpt optimize reselect <opt_id>`.

## Capabilities

### New Capabilities

- `optimization-selection`: PBO/CSCV gate, DSR computation, parameter-sensitivity scoring, post-hoc reselection.

### Modified Capabilities

- `param-optimizer`: invoke selection before `best.json`; extend `best.json` schema; add `--robust-objective`, `--pbo-threshold`, `--force`, `reselect` subcommand.

## Impact

- **Code**:
  - `python/strategy_gpt/selection/cscv.py` — CSCV split generator, PBO estimator.
  - `python/strategy_gpt/selection/dsr.py` — Deflated Sharpe (Bailey & López de Prado 2014 formula).
  - `python/strategy_gpt/selection/sensitivity.py` — k-NN neighborhood scoring over `trials.parquet` rows.
  - `python/strategy_gpt/selection/selector.py` — orchestrates CSCV → DSR → sensitivity → final ranking.
  - `python/strategy_gpt/cli.py` — wire flags; add `optimize reselect`.
- **Tests**:
  - CSCV: synthetic overfit case (random noise objective) → PBO ≈ 0.5; clearly-signal case → PBO ≈ 0.
  - DSR: hand-computable small example matches the closed-form.
  - Sensitivity: knife-edge synthetic surface → top-1 candidate's robust score is materially lower than its raw score.
  - Reselect: re-run selection against an existing `trials.parquet` produces identical output, then perturb threshold and verify the decision changes.
- **Dependencies**: `scipy.stats` (already present) for the DSR's variance-of-Sharpe estimator; no new deps.
- **Docs**: `docs/optimization.md` gains a "Selection layer" section citing Bailey/Borwein/López de Prado/Zhu 2017 (PBO), Bailey & López de Prado 2014 (DSR), López de Prado 2018 *Advances in Financial Machine Learning* ch. 11–12 (robust/sensitivity), Pardo 2008 *Evaluation and Optimization of Trading Strategies* ch. 9 (walk-forward + robustness).
- **Out of scope (this change)**:
  - GUI / plots of the OOS performance matrix or the sensitivity surface.
  - Multi-objective selection (selection is applied per primary metric; multi-metric tradeoff is already handled by `objectives` spec upstream).
  - Bootstrap confidence intervals on per-fold metrics (a follow-up could add these as input to DSR).
