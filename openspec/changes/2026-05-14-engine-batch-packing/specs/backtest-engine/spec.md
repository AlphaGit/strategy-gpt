# Spec: backtest-engine

## MODIFIED Requirements

### Requirement: Abort-on-failure for batches

The Backtest Engine SHALL support two batch failure modes selectable per `BatchSpec` via `failure_mode`:

- `abort` (default): if any run fails (panic, OOM, timeout, compile error), the engine cancels remaining runs and returns a structured failure record. The engine MUST NOT retry, resume, or skip failed runs in this mode.
- `continue`: per-run failures are recorded as structured `RunResult::Failed { run_index, error_kind, message }` entries in the result list; the engine continues dispatching remaining runs. The engine MUST NOT retry failed runs even in `continue` mode.

The selected mode applies uniformly to all runs in the batch.

#### Scenario: Mid-batch failure with abort

- **WHEN** run 47 of a 200-run batch panics under `failure_mode: abort`
- **THEN** the engine cancels remaining runs, records the failure with run index and cause, and returns failure to the caller

#### Scenario: Mid-batch failure with continue

- **WHEN** runs 47 and 132 of a 1,000-run batch panic under `failure_mode: continue`
- **THEN** the engine completes the remaining 998 runs and returns a result list with `Failed` entries at indices 47 and 132 and successful entries everywhere else

### Requirement: Batched backtest execution

The Backtest Engine SHALL accept a `BatchSpec` containing one strategy artifact, one dataset reference, and a list of run configurations, where each run specifies parameters, modes, time slice, and seed. The engine MUST compile the strategy at most once per batch and execute all runs across an internal worker pool sized to exactly `parallelism` concurrent workers. Run dispatch order MUST match submission order; the result list MUST be returned in submission-index order regardless of completion order.

#### Scenario: Many runs, one compile

- **WHEN** a `BatchSpec` contains 200 runs of the same strategy
- **THEN** the strategy is compiled exactly once and 200 run results are returned

#### Scenario: Parallel execution capped at parallelism

- **WHEN** the host has 16 cores and a `BatchSpec` declares `parallelism: 4` over 100 runs
- **THEN** at no point are more than 4 worker processes running concurrently, and all 100 results are returned

#### Scenario: Order preservation under out-of-order completion

- **WHEN** a 50-run batch is dispatched and run index 3 takes ten times longer than the others
- **THEN** the returned result list has results at every index in submission order, with index 3's slow result at position 3

#### Scenario: Large packed batch

- **WHEN** a `BatchSpec` contains 10,000 runs of the same strategy under `failure_mode: continue`
- **THEN** the strategy is compiled exactly once, all 10,000 results are returned in submission order, and the wall-clock time scales as `(10,000 / parallelism) × per_run_time` modulo worker-pool startup overhead

### Requirement: Determinism

Given identical strategy artifact hash, dataset manifest, parameters, modes, seed, and `failure_mode`, the engine SHALL produce a byte-identical result list across runs. Under `failure_mode: continue`, deterministically failing runs MUST carry the same `error_kind` and `message` across reruns.

#### Scenario: Deterministic replay

- **WHEN** the same `BatchSpec` is executed twice on the same machine
- **THEN** the two result lists compare equal

#### Scenario: Deterministic failure replay

- **WHEN** a `BatchSpec` under `failure_mode: continue` includes a run that deterministically panics and the spec is executed twice
- **THEN** both runs produce identical `Failed { run_index, error_kind, message }` entries at the same index
