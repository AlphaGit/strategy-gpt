# 0011 — PBO threshold default 0.5

## Context

The selection layer computes Probability of Backtest Overfitting (PBO) via Combinatorially Symmetric Cross-Validation and rejects the run when PBO exceeds a threshold. The threshold is a tunable knob; the question is what default to ship.

## Decision

The default `selection.pbo.threshold` is **0.5**. Operators can override per-run via `--pbo-threshold` or in the experiment spec, and can re-evaluate post-hoc via `strategy-gpt optimize reselect`.

## Consequences

- A PBO of 0.5 corresponds to "the in-sample winner lands in the bottom half of the OOS ranking at least as often as the top half" — the boundary between "no edge detected" and "systematically worse than chance". Rejecting above this line is the methodologically defensible default.
- Lower thresholds (0.3, 0.4) reject more runs, biasing the platform toward conservatism; higher thresholds (0.7, 0.8) reject fewer, biasing toward publishing peaks.
- The threshold is recorded in the manifest. Overrides are recorded too, so reviewers can audit which decisions used non-default thresholds.
- `--force` lets an operator publish despite rejection; the override is permanent in the manifest and the rejection is preserved.

## Alternatives Considered

- **0.5 default with per-strategy override only.** Considered; rejected because the CLI override is sometimes needed for ad-hoc inspection without rewriting the spec.
- **Lower default (0.3).** Cleaner publish rate but bites legitimate experiments that happen to live near the boundary; we prefer the methodologically central value.
- **No default (require explicit configuration).** Friction without payoff; most research runs use the central threshold.

## Status

accepted
