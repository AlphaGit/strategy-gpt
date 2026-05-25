## ADDED Requirements

### Requirement: Mechanical failures are deferred, not rejected

A candidate whose code emission fails mechanically — `reject_build`, `reject_lint`, `reject_format`, `reject_deps`, or `exhausted_repair_budget` on any of stages 1-3 — MUST be persisted to the per-strategy ledger as a `deferred` decision, NOT as a `rejected` decision. The candidate's idea and stage-2 commitments are preserved so a future run can re-attempt the same hypothesis with cleaner code.

The orchestrator's prior-decision projection (consumed by `cheap_critique` and stage-1 ideation) MUST skip `deferred` entries so the duplicate-similarity check and the stage-1 prior-decisions prompt do not bias future runs against the same logic. Logic-level rejections (`reject_schema`, `reject_smoke`, `reject_noise`, `reject_variance`, `reject_verdict`) continue to persist as `rejected` and continue to feed the prior-decision filter.

Mechanical failures DO consume an iteration of the inner-loop budget — the loop must not spin forever — but they do NOT count against the spec's logic-rejection accounting.

#### Scenario: Stage-3 build failure persists as deferred

- **WHEN** stage-3 emission exhausts its repair budget against repeated `cargo build` failures
- **THEN** the candidate is persisted with `DecisionStage.kind = "deferred"`; the `outcome.stage` field carries the structural reject kind (`reject_build` or `exhausted_repair_budget`); the iteration counter increments but the candidate's idea does NOT appear in the next iteration's prior-decisions prompt

#### Scenario: Stage-3 build failure does not bias cheap_critique

- **WHEN** a later run loads prior decisions and a deferred candidate's stage-1 idea is structurally similar to a fresh stage-1 proposal
- **THEN** `cheap_critique` does NOT reject the fresh proposal on duplicate-similarity grounds; the deferred candidate is invisible to the duplicate-detector

#### Scenario: Logic rejection persists as rejected

- **WHEN** a candidate clears build / lint / smoke but fails `reject_verdict` after the mechanical gate passes
- **THEN** the candidate is persisted with `DecisionStage.kind = "rejected"` and IS visible to the prior-decision filter for future ideation

#### Scenario: Operator sees deferred count in CLI progress

- **WHEN** the CLI runs `strategy-gpt hypothesize <name>` with progress streaming on (default) and at least one candidate is deferred
- **THEN** the rank line surfaces the deferred count distinctly from rejected (e.g. `0 accepted, 2 rejected, 1 deferred`); the stage-3 failure line labels mechanical failures as `deferred (mechanical: hypothesis preserved)` rather than `failed`
