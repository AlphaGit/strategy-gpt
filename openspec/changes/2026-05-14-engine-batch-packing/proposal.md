# Engine batch packing — pack many candidates per submission

## Why

The parameter optimizer will dispatch hundreds to millions of candidate parameter sets, each evaluated against multiple folds. Submitting one candidate at a time forces a fresh PyO3 boundary cross, JSON serialization of the BatchSpec, and engine-side per-batch overhead for every single candidate, even though they share the same artifact and dataset. The engine already accepts a `BatchSpec` with a `runs: [...]` list; what's missing is the contract and behavior that makes packing thousands of runs per submission a first-class path.

Two specific gaps:

1. **Failure semantics.** The current spec says the engine aborts the entire batch on any run failure. That is correct for the `run` command (loud failure, no partial results), but fatal for the optimizer (one bad candidate must not kill the sweep).
2. **Worker-pool contract.** The engine spec promises parallel execution but does not pin the dispatch order or the worker-pool sizing rules the optimizer needs to predict wall time and to reason about determinism across reruns.

This change tightens those contracts so the optimizer can rely on them, without breaking the `run` command's existing loud-failure default.

## What Changes

- **MODIFIED capability** `backtest-engine`:
  - Add `BatchSpec.failure_mode: "abort" | "continue"` (default `abort` — preserves current behavior). Under `continue`, per-run failures are recorded as structured `RunResult.failed { error }` entries and the batch proceeds.
  - Pin dispatch order to submission order: workers pull runs in the order they appear in `runs`; results are returned in input order regardless of completion order.
  - Cap worker-pool size to the resolved `parallelism` integer. Engine MUST NOT spawn more than `parallelism` concurrent workers.
  - Strengthen the determinism scenario: for `failure_mode=continue`, identical inputs (including a deterministic set of failing runs) MUST yield byte-identical result lists.
  - Add an explicit "large packed batch" scenario at ≥ 10,000 runs that exercises the artifact-compile-once promise and the worker-pool throughput.

## Capabilities

### Modified Capabilities

- `backtest-engine`: add `failure_mode`, pin dispatch order, cap worker-pool size to `parallelism`, add large-batch scenario.

## Impact

- **Code**: `crates/engine` — `BatchSpec` gets `failure_mode` field. Coordinator wraps each worker invocation; on `continue`, panics/OOM/timeouts populate a structured `RunResult.failed` instead of bubbling up. PyO3 binding mirrors the field.
- **Compat**: Default `abort` preserves existing `strategy-gpt run` behavior.
- **Tests**: Add a 1,000-run mixed pass/fail batch test; add a 10,000-run smoke (using the no-op example strategy) to validate dispatch order, single compile, and worker cap.
- **Out of scope (this change)**: streaming partial results, dynamic worker scaling, cross-host distribution, the optimizer itself.
