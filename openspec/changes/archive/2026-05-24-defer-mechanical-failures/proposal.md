## Why

When stage-3 code emission burns through its repair budget against a `reject_build` / `reject_lint` / `reject_format` / `reject_deps` / `exhausted_repair_budget` failure, the candidate ends up in the per-strategy ledger as a hard `rejected` decision. The next iteration's `cheap_critique` then treats that candidate's idea as a *logic* rejection — the duplicate-similarity check biases against re-proposing similar hypotheses, and the persisted rationale is wired into stage-1's prior-decisions prompt as "this idea was rejected."

That is the wrong signal. A build failure means the LLM couldn't translate the hypothesis into compiling Rust; it says nothing about whether the underlying idea (e.g. "add a time-based exit cap to force closes") is good. The current code wastes the iteration *and* poisons future runs against re-trying the same idea with cleaner code.

## What Changes

- Add a `DEFERRED` member to `DecisionKind` for candidates whose code emission failed mechanically. The idea + commitments are preserved; the loop records the failure so the operator can see what was tried, but the hypothesis is NOT treated as a logic rejection.
- Add `is_mechanical(reject_kind)` in `reject_taxonomy` covering `{reject_build, reject_lint, reject_format, reject_deps, exhausted_repair_budget}`. `reject_schema` and `reject_smoke` stay logic-level — schema mismatches mean the LLM's `param_intent` disagrees with the artifact's declared params (an idea-level error); smoke failures (panic / no trades / sanity trip) mean the compiled strategy doesn't behave (also idea-level).
- `RejectedHypothesis` carries a new `reject_kind` field. The workflow's `rank_step` populates it; the orchestrator's persistor branches on `is_mechanical` to write `DecisionKind.DEFERRED` instead of `REJECTED`.
- `_project_prior_decisions` skips `DEFERRED` entries so `cheap_critique` and stage-1 ideation do not see them as logic priors.
- CLI progress renderer labels mechanical failures as "deferred (mechanical: hypothesis preserved)" and reports the deferred count alongside accepted/rejected in the rank line.

## Capabilities

### Modified Capabilities

- `hypothesis-loop`: add a requirement covering the mechanical-vs-logic distinction in decision persistence and the prior-decision filter.

## Impact

- Modified: `python/strategy_gpt/types.py` — add `DecisionKind.DEFERRED`.
- Modified: `python/strategy_gpt/reject_taxonomy.py` — add `is_mechanical` predicate.
- Modified: `python/strategy_gpt/hypothesis_loop.py` — `RejectedHypothesis.reject_kind` field.
- Modified: `python/strategy_gpt/workflow.py` — `rank_step` populates `reject_kind`.
- Modified: `python/strategy_gpt/hypothesize.py` — persistor branches on `is_mechanical`; `_project_prior_decisions` skips deferred.
- Modified: `python/strategy_gpt/cli.py` — progress renderer labels deferred outcomes distinctly.
- No on-disk migration — existing ledger rows continue to read as `rejected` because they never carry the new `deferred` tag. The change is forward-only.
- Iteration budget mechanics are unchanged. Mechanical failures still consume an iteration so the loop does not spin forever; only the persistence + future-ideation semantics change.
