# 0020 — Comparative falsification with a variance-aware acceptance floor

## Context

The hypothesis loop scores each candidate against a baseline-best result and
asks: did this candidate beat the baseline by enough to matter? The naive
answer — "candidate aggregate score > baseline aggregate score" — is wrong in
two directions.

It is **too permissive** under high per-fold variance: a candidate that beats
the baseline by 0.05 sharpe when the per-fold standard deviation is 0.20
sharpe has not measurably beaten the baseline. Accepting at this signal-to-noise
ratio produces a steady drip of false positives whose effects do not replicate
on the next dataset.

It is **too restrictive** for candidates that move metrics the LLM did not
claim. A candidate that meets its primary claim but blows up a guard metric
(e.g. wins sharpe, doubles drawdown) is structurally a regression even when
the score nominally improved. The single "score beats baseline" test cannot
distinguish *which* metric the LLM was claiming to move.

Earlier scaffolding used a fixed-threshold falsification ("metric >=
threshold") with no per-fold variance check and no guard constraints. That
shape pre-dates the rewrite that turned the loop into a strategy-logic search;
it works for single-shot parameter tweaks but is the wrong granularity for
multi-fold candidate evaluation against a noisy baseline.

## Decision

Adopt **comparative falsification with a variance-aware acceptance floor.**
The contract has three pieces, evaluated in this order:

### 1. Mechanical gate (variance-aware, deterministic, non-overridable)

```
σ_combined  = sqrt(σ_candidate² + σ_baseline²)        # population stddev
score_floor = (cand_aggregate - base_aggregate) > k · σ_combined
fold_cv     = σ_candidate / |cand_aggregate|
variance_ok = fold_cv < cv_threshold
accept_gate = score_floor AND variance_ok
```

Defaults: `k = 1.0` (≈68% Gaussian confidence), `cv_threshold = 0.5`. Both
are configurable. The gate is implemented in
`python/strategy_gpt/mechanical_gate.py` and runs after the per-candidate
mini-optimize.

The gate is a **hard floor**. No downstream node (including the
verdict-critique LLM) may reverse a gate rejection. A genuinely strong but
borderline candidate is allowed to re-emerge in a later iteration with
cleaner evidence, where it can clear the gate cleanly.

### 2. Primary falsification claim (LLM-stated, comparative)

The candidate carries a structured claim:

```yaml
primary:
  metric: <name>
  direction: gt | gte | lt | lte
  delta_vs_baseline: <float>
  scope: aggregate | regime:<label> | fold:<idx> | window:<start>:<end>
```

Evaluated by `tester.attempt_with_optimize` against the baseline-best result
on the same `dataset_manifest`. Scope-restricted claims are evaluated against
the baseline on the same scope, so the comparison stays apples-to-apples.

### 3. Guard constraints (LLM-stated)

Zero or more constraints the candidate promises NOT to break:

```yaml
guard_constraints:
  - { metric: max_drawdown, direction: lte, delta_vs_baseline: 0.05 }
  - { metric: trade_count,  direction: gte, factor: 0.5 }
```

A guard failure classifies the candidate as **regression** regardless of the
primary-claim outcome.

The full verdict matrix:

| Δ beats σ | claim met | guards held | verdict     |
|-----------|-----------|-------------|-------------|
|   ✓       |   ✓       |    ✓        | accepted (subject to verdict-critique) |
|   ✓       |   ✓       |    ✗        | regression  |
|   ✓       |   ✗       |   any       | falsified — outcome real, prediction wrong |
|   ✗       |   any     |   any       | noise (reject_noise) — gate rejects regardless of claim |

The `falsified` vs `noise` distinction is a learning signal: a falsified
candidate did something measurable but the LLM read it wrong; a noise
candidate did not measurably do anything.

## Consequences

**Pros**

- Variance-aware floor catches small-delta candidates that win by luck on a
  noisy fold.
- Guard constraints catch "won sharpe but blew up drawdown" regressions that
  a single-metric score check would silently accept.
- The four-way verdict matrix gives the next-iteration generate prompt a
  richer signal than a binary pass/fail.
- The mechanical gate is replay-safe (no LLM involvement); the LLM-stated
  claims are a separate, auditable layer.

**Cons**

- More configuration surface: `k`, `cv_threshold`, per-objective overrides.
  Default values are placeholders; the first VXX run will inform tuning.
- A genuinely strong but high-variance candidate is rejected. Mitigated by
  the borderline flag (surfaced to verdict-critique) and by the loop's
  ability to re-emit the same idea in a later iteration with cleaner folds.
- Guard-constraint authoring puts more burden on the LLM prompt. Mitigated
  by stage-2's prompt requiring a guard for any metric the LLM expects to
  move significantly.

## Alternatives Considered

- **Fixed-threshold falsification (legacy).** Rejected as noted in Context:
  no per-fold variance check, no guards, wrong granularity for the rewrite.
- **CSCV / PBO overfit detection inside hypothesize.** Rejected: sample size
  (K candidates × iterations × folds) is too small for these statistics to
  be meaningful. Overfit detection remains the job of downstream `optimize`.
- **LLM-only verdict.** Rejected: the variance-aware floor is the only
  statistically motivated reject in the loop. Letting the LLM override it
  re-opens the false-positive surface we set out to close.
- **Tiered cheap-then-expensive evaluation.** Considered. Rejected: another
  tuning surface (borderline detector) for unclear benefit while engine
  throughput is acceptable.

## Status

Accepted (2026-05-20).
