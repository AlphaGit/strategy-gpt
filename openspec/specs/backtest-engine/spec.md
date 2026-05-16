# Spec: backtest-engine

## Purpose

Rust-native backtest engine that runs batches of strategy executions, supports stress and sensitivity modes, and emits enriched result frames (trades, signals fired and suppressed, decision log, equity curve, regimes). The engine is a research harness only; it does not route real orders or manage live positions.

## Requirements

### Requirement: Backtest-only scope

The Backtest Engine is a **research harness**: it simulates strategy execution against historical bars to produce evaluation artifacts. It SHALL NOT route real orders, connect to brokers, manage live positions, or operate in real time. Every "order", "fill", "position", and "risk cap" referenced elsewhere in this spec is a *simulated* concept evaluated against cached historical data, not a live-trading construct.

#### Scenario: Engine receives a real-time market connection request

- **WHEN** any caller attempts to configure the engine for live, real-time, or paper-trading execution
- **THEN** the API rejects the request; the engine accepts only `BatchSpec` requests against cached historical datasets

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

### Requirement: Stress-test modes

The engine SHALL natively support stress-test modes selectable per run:

- `MonteCarlo { n, block_size }` — block-bootstrap resamples of the input bars.
- `Slippage { bps_grid }` — applies each slippage value to every fill.
- `RegimeFilter { ranges }` — restricts execution to specified historical ranges.

Stress modes MUST appear as entries in the run list and produce sub-results within `BacktestResult.stress`.

#### Scenario: Monte Carlo block bootstrap

- **WHEN** a run is configured with `MonteCarlo { n: 1000, block_size: 20 }`
- **THEN** the engine executes 1000 backtests over block-resampled bars and returns aggregated metrics with confidence intervals in `stress`

### Requirement: Sensitivity sweeps

The engine SHALL natively support parametric sensitivity sweeps where one or more parameters are varied across a grid, holding others fixed. Sensitivity output MUST include the surface of each metric across the swept dimension(s).

#### Scenario: Single-parameter sweep

- **WHEN** a sensitivity run specifies `param="vol_lo"` over `range=5..20 step=1`
- **THEN** `BacktestResult.sensitivity` contains 16 result points keyed by `vol_lo` value

### Requirement: Enriched result schema

The engine SHALL return a `BacktestResult` — a research artifact summarizing one simulated run — containing:

- `metrics`: Sharpe, Sortino, Profit Factor, Win Ratio, Max Drawdown, Annualized Return, trade-length statistics.
- `trades`: every closed simulated trade with entry/exit timestamps, side, size, pnl, entry reason, exit reason, and snapshot of active signals at entry.
- `signals`: every signal evaluation with timestamp, name, value, `fired` flag, and optional `suppressed_by` reference.
- `equity`: per-bar equity, drawdown, and exposure.
- `regimes`: post-hoc regime annotations.
- `exec_log`: ordered decision events including blocked entries, filtered signals, and engine sanity-bound interventions (a backtest-validity bound, not a live risk-management feature).
- `meta`: strategy artifact hash, dataset manifest hash, seed, runner version.

`stress` and `sensitivity` MUST be present when the corresponding modes ran.

#### Scenario: Suppressed signal recording

- **WHEN** a signal evaluates to `fire=true` but is blocked by a downstream filter
- **THEN** the result's `signals` array contains the evaluation with `fired=false` and `suppressed_by` set to the filter name

#### Scenario: Decision log captures non-trade events

- **WHEN** the strategy considers an entry but the engine's sanity-bound layer blocks it (e.g., size would exceed a configured backtest validity ceiling)
- **THEN** an event recording the bound intervention appears in `exec_log` even though no simulated trade was opened

### Requirement: Worker process isolation

Strategy execution SHALL run in a worker process separate from the orchestrator. A worker crash, OOM, or timeout MUST NOT take down the orchestrator. The engine MUST enforce per-run time and memory caps via OS primitives and kill workers that exceed them.

#### Scenario: Worker panic during a run

- **WHEN** a strategy panics in `on_bar`
- **THEN** the worker process exits, the engine records a structured failure for that run, the batch aborts (per Decision 5), and the orchestrator remains alive

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

### Requirement: Determinism

Given identical strategy artifact hash, dataset manifest, parameters, modes, seed, and `failure_mode`, the engine SHALL produce a byte-identical result list across runs. Under `failure_mode: continue`, deterministically failing runs MUST carry the same `error_kind` and `message` across reruns.

#### Scenario: Deterministic replay

- **WHEN** the same `BatchSpec` is executed twice on the same machine
- **THEN** the two result lists compare equal

#### Scenario: Deterministic failure replay

- **WHEN** a `BatchSpec` under `failure_mode: continue` includes a run that deterministically panics and the spec is executed twice
- **THEN** both runs produce identical `Failed { run_index, error_kind, message }` entries at the same index

### Requirement: PyO3 control-plane bindings

The engine's control plane (submit batch, query status, cancel) SHALL be exposed to Python via PyO3 in-process. Strategy execution itself MUST remain in worker processes; only orchestration calls cross the PyO3 boundary in-process.

#### Scenario: Python orchestrator submits a batch

- **WHEN** Python calls the PyO3-bound `submit_batch(spec)` function
- **THEN** the engine validates the spec, spawns workers, and returns a handle the orchestrator can poll

### Requirement: Fill model is internal; strategies submit intents only

The engine SHALL apply a configurable fill model (e.g., `next-bar-open`, `current-bar-close`) to each submitted trade intent. The fill model is engine configuration, not a strategy parameter. Strategies SHALL NOT see, query, or manipulate any pending-order state; their only view is their position via `Context::get_position` and any `on_fill` callback when an intent fills.

#### Scenario: Limit intent that does not reach price

- **WHEN** a strategy submits a limit intent that the configured fill model determines never fills within its evaluation window
- **THEN** the engine records the intent's expiry in `exec_log` with reason `intent_expired_unfilled` and the strategy's position is unchanged; no `on_fill` is invoked

#### Scenario: Strategy queries pending orders

- **WHEN** a strategy attempts to enumerate or inspect pending intents
- **THEN** no such API exists on `Context`; the strategy compiles only against position, indicators, signals, decisions, and reproducible state
