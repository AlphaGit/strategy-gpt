# Spec: experiment-spec

## Purpose

Single declarative file (`experiment-spec.yaml` or `.json`) that fully describes a strategy-gpt backtest experiment. It is the input to `strategy-gpt run --spec` and is the artifact future changes (`optimization-spec`, `optimize-command`) extend with search and fold blocks. The spec captures the strategy artifact, bars source, engine configuration, run list, parallelism, and resource caps; the orchestrator translates it to an internal `BatchSpec` before submitting to the engine.

## Requirements

### Requirement: Single-file experiment definition

A backtest experiment SHALL be fully defined by a single declarative file (`experiment-spec.yaml` or `.json`). The file MUST identify: the compiled strategy artifact, the bars source, the engine configuration, the run list (params, modes, seed, slice per run), parallelism, and resource caps. No additional CLI flags or out-of-band inputs are required to reproduce the run.

#### Scenario: Self-contained experiment file

- **WHEN** `strategy-gpt run --spec experiment.yaml` is invoked with no other run-shaping flags
- **THEN** the engine receives an internal `BatchSpec` that is fully determined by the file's contents, and the resolved spec recorded into the ledger is sufficient (with the local cache and the artifact) to reproduce the run

### Requirement: Polymorphic bars reference

The `bars` block SHALL accept exactly one of: `{dataset: <manifest_hash>}` referencing an already-cached dataset, or `{request: BarRequest}` describing a fetch via the data gateway. When `request` is provided and the resulting manifest is not yet cached, the runner MUST fetch through the gateway with `prefer_cache` semantics before submitting to the engine.

#### Scenario: Auto-fetch on cache miss

- **WHEN** an experiment-spec declares `bars: {request: {provider: yfinance, symbol: VXX, start: ..., end: ...}}` and the corresponding manifest is not in the cache
- **THEN** the runner fetches the dataset through the gateway, records the resolved manifest hash, and proceeds to submit the run

#### Scenario: Both bars variants rejected

- **WHEN** an experiment-spec declares both `bars.dataset` and `bars.request`
- **THEN** the loader rejects the spec at validation time with a structured error before any side effect

### Requirement: Auto parallelism resolution

`parallelism` SHALL accept either a positive integer or the literal string `auto`. When `auto`, the runner MUST resolve it at load time to `max(1, usable_cpu_count - 1)`, where `usable_cpu_count` honors OS-level affinity (e.g., `sched_getaffinity` on linux). The resolved integer MUST be recorded into the ledger; the literal `auto` is not persisted as such.

#### Scenario: Auto on a cgroup-restricted host

- **WHEN** an experiment runs on a host whose process is restricted to 4 CPUs via cgroup/taskset and `parallelism: auto` is declared
- **THEN** the runner resolves `parallelism` to `3` and records `3` in the ledger

### Requirement: Slippage is not an engine-config field

The `engine` block of an experiment-spec SHALL NOT contain `slippage_bps`. Per-fill slippage MUST be expressed as a stress mode entry on a run. The loader SHALL reject specs that include `slippage_bps` under `engine` with a structured migration error.

#### Scenario: Legacy slippage_bps rejected

- **WHEN** an experiment-spec declares `engine.slippage_bps: 1.5`
- **THEN** validation fails with a message instructing the user to express slippage as a `Slippage { bps_grid }` mode on the affected run(s)

### Requirement: Legacy batch.json rejected

The runner SHALL reject the pre-existing `batch.json` shape (top-level `strategy`, `dataset`, `runs` without an enveloping experiment block) with an explicit migration error referencing the new schema. The runner MUST NOT silently coerce legacy files.

#### Scenario: Legacy file rejected with migration guidance

- **WHEN** `strategy-gpt run --spec legacy_batch.json` is invoked with a file matching the legacy shape
- **THEN** the runner exits non-zero with a message identifying the legacy format and pointing at the `experiment-spec.yaml` documentation
