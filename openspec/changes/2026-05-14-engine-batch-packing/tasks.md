## 1. BatchSpec schema

- [ ] 1.1 Add `failure_mode: FailureMode` to `BatchSpec` in `crates/engine` with `FailureMode = Abort | Continue` and default `Abort`.
- [ ] 1.2 Update serde derives, JSON Schema (if any), and PyO3 binding to expose the new field.
- [ ] 1.3 Update `python/strategy_gpt/types.py` `BatchSpec` pydantic model with the new field.

## 2. Coordinator behavior

- [ ] 2.1 Refactor the run dispatcher to maintain a FIFO of submitted run indices; workers pull in submission order.
- [ ] 2.2 Result aggregator places each `RunResult` at its submission index regardless of completion order.
- [ ] 2.3 Worker-pool size = `parallelism`; the coordinator MUST NOT spawn additional workers beyond that cap.

## 3. Failure isolation under `continue`

- [ ] 3.1 Wrap each worker invocation; on panic / OOM / timeout / non-zero exit, build a `RunResult::Failed { run_index, error_kind, message }` and continue dispatching.
- [ ] 3.2 On `failure_mode: abort`, preserve existing behavior — first failure cancels remaining runs.
- [ ] 3.3 Ensure determinism: failures under `continue` carry the same error_kind + message across reruns for the same input.

## 4. Tests

- [ ] 4.1 Unit test: order-preserving aggregation when workers finish out of order.
- [ ] 4.2 Integration test: 1,000-run packed batch with the example no-op strategy, `failure_mode: continue`, injected failures at indices 0, 499, 999; verify dispatch order, failure isolation, single compile.
- [ ] 4.3 Integration test (`#[ignore]`d, opt-in): 10,000-run smoke, measure single-compile contract and saturation of `parallelism` workers.
- [ ] 4.4 Determinism test: same input twice → byte-identical result list including failures.

## 5. PyO3 + Python

- [ ] 5.1 Expose `failure_mode` through the engine PyO3 binding.
- [ ] 5.2 Add a Python integration test that submits a 200-run batch with one failing index under `continue` and verifies the failed entry has the expected schema.

## 6. Docs

- [ ] 6.1 Update `crates/engine/README.md` (or capability docs) describing `failure_mode` and the dispatch-order guarantee.
- [ ] 6.2 Note the new behavior in `docs/cli-cookbook.md` near the `run` recipe (default still `abort`, so most users see no change).
