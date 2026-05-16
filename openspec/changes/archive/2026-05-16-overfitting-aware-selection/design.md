# Design — overfitting-aware selection

## 1. Pipeline

```
optimizer produces (params, per-fold OOS metrics, score) for every trial
                       │
                       ▼
            CSCV / PBO computation              ──┐
                       │                          │
                       ▼                          │ all three
            Deflated Sharpe per top-K  ───────────┤ run on
                       │                          │ top-K
                       ▼                          │ candidates
            Sensitivity score per top-K           │
                       │                          │
                       ▼                        ──┘
            Decision:
              if PBO > threshold:    reject (unless --force)
              if --robust-objective: rank by sensitivity score
              else:                  rank by DSR (tie-break: raw score)
              final = top-1 of ranked list
                       │
                       ▼
            best.json  (+ pbo, deflated_sharpe, sensitivity_score, decision)
```

The selection layer is a *pure function* of `trials.parquet` + `manifest.json` + selection knobs. Post-hoc re-runs (`optimize reselect <opt_id>`) produce identical output for identical inputs.

## 2. CSCV / PBO

Combinatorially Symmetric Cross-Validation (Bailey, Borwein, López de Prado, Zhu 2017).

Setup: a `(N, S)` matrix M where N = trials retained (top-K), S = folds. `M[i, j]` = OOS metric of trial i on fold j.

Procedure:

```
1. Partition the S folds into S/2 of equal size; require S even (drop tail fold if odd).
2. For every combinatorial split of folds into two disjoint halves (A, B) of size S/2 each:
       a. IS_i  = mean(M[i, A])
       b. OOS_i = mean(M[i, B])
       c. i*    = argmax_i IS_i
       d. logit_omega = logit(rank(OOS_{i*}) / (N+1))    # rank of i* in OOS, normalized
3. PBO = fraction of splits where logit_omega < 0
   (i.e., the IS-best trial is in the bottom half of OOS)
```

Practical: number of splits = `binom(S, S/2)` — for S=8 that's 70, for S=12 that's 924. Beyond S=16 sample splits rather than enumerate.

Threshold default: `0.5`. Rationale: PBO > 0.5 means the IS-best is *worse than chance* on OOS, the classic overfitting signal.

Knobs:

```yaml
optimize:
  selection:
    pbo:
      enabled: true
      threshold: 0.5
      top_k: 50                     # how many trials to feed into M
      max_splits: 4096              # sample splits beyond this
```

## 3. Deflated Sharpe Ratio

Bailey & López de Prado 2014. For the primary Sharpe of trial i across N effective trials:

```
DSR_i = Z((SR_i - E[max SR | N, skew, kurt]) / sqrt(Var(SR_i)))
```

Where:
- `E[max SR]` uses the Bailey/López de Prado approximation:
  `E[max SR | N] ≈ sqrt((1 - γ) · Φ⁻¹(1 - 1/N) + γ · Φ⁻¹(1 - 1/(N·e)))` with `γ` Euler-Mascheroni.
- `Var(SR_i)` accounts for non-normality:
  `Var(SR_i) = (1 - skew·SR_i + (kurt-1)/4 · SR_i²) / (T - 1)` where T is trade count.
- `Z` is the standard-normal CDF; DSR is reported as a probability in `[0, 1]` (P(true SR > 0)).

Effective N: number of *distinct* parameter sets evaluated (not raw trial count, which includes fold replication). Recorded in manifest.

Re-ranking: top-K candidates ranked by DSR descending. Ties broken by raw primary score.

Knobs:

```yaml
optimize:
  selection:
    deflated_sharpe:
      enabled: true
      top_k: 50
      effective_n: distinct_params  # distinct_params | trial_count
```

## 4. Parameter-sensitivity (robust) score

Practitioner consensus from López de Prado (AFML ch. 11–12) and Pardo (2008 ch. 9): a knife-edge peak in the parameter surface rarely survives live trading because the optimizer's effective sample size is small relative to the parameter grid density.

For each top-K candidate i:

```
N_i = k nearest neighbors of params_i in normalized parameter space
      (Euclidean over min-max-normalized dims; categorical → 0/1 distance)
robust_score_i = mean(score over N_i) - lambda * std(score over N_i)
```

Where:
- `k` = `neighborhood_k` (default 8).
- `lambda` = `sensitivity_penalty` (default 1.0).
- Self-inclusion in the neighborhood: yes — the candidate's own score participates in the mean.
- Neighborhood drawn from the *full* `trials.parquet`, not just the top-K — this leverages the search history.

Two use modes:

- **Reporting** (always on): `robust_score` is reported in `best.json` for the top-K alongside raw score.
- **Selection** (opt-in via `optimize.robust_objective: true` or `--robust-objective`): final selection ranks by `robust_score` instead of DSR.

Important constraint: robust scoring is applied *only* at selection time, **not** during the search itself. Mixing robust scoring into the per-fold search would smear the search method's convergence dynamics and force every method to maintain a k-NN index over its own history; that complexity is rejected in favor of post-hoc application.

Knobs:

```yaml
optimize:
  selection:
    sensitivity:
      enabled: true
      neighborhood_k: 8
      penalty: 1.0                  # lambda
  robust_objective: false           # opt-in for selection-by-robust-score
```

## 5. Final decision logic

```python
def final_decision(top_k, pbo, robust_objective_flag, force):
    if pbo > pbo_threshold and not force:
        return Decision(status="rejected_pbo", best=None, reason=f"PBO={pbo} > {threshold}")
    if robust_objective_flag:
        ranked = sorted(top_k, key=robust_score_desc)
    else:
        ranked = sorted(top_k, key=deflated_sharpe_desc, then_raw_score_desc)
    if not ranked:
        return Decision(status="rejected_constraint", best=None, reason="all top-K rejected")
    return Decision(status="accepted", best=ranked[0])
```

`best.json` always records the decision plus `pbo`, `deflated_sharpe`, `sensitivity_score`, even when the decision is `rejected_pbo`. The rejected `best` candidate is still recorded as `would_have_picked` for transparency.

## 6. Post-hoc reselection

`strategy-gpt optimize reselect <opt_id> [--robust-objective] [--pbo-threshold T] [--top-k K]`

Reads `trials.parquet` and `manifest.json`, runs the selection pipeline with the override knobs, writes a new `best_<timestamp>.json` next to the original (never overwrites the original; preserves audit trail). Supports comparing two reselections via `optimize compare <opt_id> <best1> <best2>`.

Required because: thresholds and robust-objective flags are tuning knobs that the user may want to adjust after seeing initial results, without re-running the search.

## 7. CSCV details deferred

- For S=10 to S=16 we enumerate all `binom(S, S/2)` splits.
- For S > 16 we Monte Carlo sample `max_splits` splits with a seeded RNG.
- The Monte Carlo seed is recorded in the manifest for replay.

## 8. Citations recorded in artifacts

Each output artifact's manifest section records the citations used so the methodology is auditable:

```json
{
  "selection_methodology": {
    "pbo": "Bailey, Borwein, López de Prado, Zhu (2017), 'The Probability of Backtest Overfitting', J. Computational Finance",
    "dsr": "Bailey, López de Prado (2014), 'The Deflated Sharpe Ratio', J. Portfolio Management",
    "sensitivity": "López de Prado (2018), 'Advances in Financial Machine Learning' ch. 11–12 (Wiley); Pardo (2008), 'The Evaluation and Optimization of Trading Strategies' ch. 9 (Wiley)"
  }
}
```

## 9. Why these belong above the search, not inside it

The literature is consistent: anti-overfitting wrappers operate on the *output* of a search, because they need the full trial set to estimate multiple-testing bias (DSR), to compare IS vs OOS rankings (PBO), or to estimate local-neighborhood variance (sensitivity). Folding any of these into the search loop would require the search method to maintain its own anti-overfitting state, conflate convergence dynamics with selection criteria, and prevent post-hoc reselection — a critical research workflow.

This is why the change introduces a *new capability* (`optimization-selection`) rather than extending `param-optimizer`'s internals.
