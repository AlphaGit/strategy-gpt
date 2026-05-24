## 1. Decision taxonomy

- [x] 1.1 Add `DecisionKind.DEFERRED` to `python/strategy_gpt/types.py`
- [x] 1.2 Add `is_mechanical(kind)` predicate covering `{reject_build, reject_lint, reject_format, reject_deps, exhausted_repair_budget}` in `python/strategy_gpt/reject_taxonomy.py`

## 2. Workflow + orchestrator

- [x] 2.1 Add `RejectedHypothesis.reject_kind: str | None` in `python/strategy_gpt/hypothesis_loop.py`
- [x] 2.2 `workflow.rank_step` populates `reject_kind` from `state["candidate_reject_kind"]`
- [x] 2.3 `hypothesize._persist_candidate` writes `DecisionKind.DEFERRED` when `is_mechanical(reject_kind)`
- [x] 2.4 `hypothesize._project_prior_decisions` skips entries with `outcome.kind == "deferred"`

## 3. CLI

- [x] 3.1 Progress renderer labels stage-3 mechanical failures as `deferred (mechanical: hypothesis preserved)`
- [x] 3.2 Rank line surfaces deferred count distinctly from rejected

## 4. Tests

- [x] 4.1 `is_mechanical` predicate test covering the full reject taxonomy
- [x] 4.2 Persistor branches on mechanical kinds to write `deferred`
- [x] 4.3 Prior-decision projection skips deferred entries

## 5. Quality gates

- [x] 5.1 `make lint-python` clean
- [x] 5.2 `make test-python` clean
