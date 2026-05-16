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

### Requirement: Optional optimize block

An experiment-spec MAY include an `optimize` block declaring a parameter search over the experiment's run template. The block SHALL declare: `method` (one of `recursive_grid`, `grid`, `random`, `bayesian`), `seed`, `aggregator` (currently `mean`), a `space` map of per-parameter shapes (`{type: float|int|choice, ...}`), method-specific knob sub-blocks, and a `persist` sub-block (`root`, `name`). When `optimize` is present, the spec MUST also include a `folds` block.

#### Scenario: Spec with optimize requires folds

- **WHEN** an experiment-spec declares an `optimize` block but no `folds` block
- **THEN** validation fails with a structured error naming the missing block

#### Scenario: Search space disjoint from fixed params

- **WHEN** an experiment-spec declares both `runs[0].params.vol_lo: 0.3` and `optimize.space.vol_lo: {type: float, low: 0.1, high: 0.6}`
- **THEN** validation fails with a structured error reporting the conflicting key

### Requirement: Optional folds block

An experiment-spec MAY include a `folds` block declaring how to split the experiment's time slice into training and out-of-sample segments. The block SHALL declare: `count` (≥ 2), `scheme` (`rolling` or `anchored`), `gap` (≥ 0 bars between train end and OOS start), and optional `warmup_bars` (≥ 0). When present, the runner MUST derive `count` train/OOS pairs from the experiment's base slice according to the scheme.

#### Scenario: Rolling fold derivation

- **WHEN** an experiment-spec declares `folds: {count: 4, scheme: rolling, gap: 0}` over a base slice spanning eight years
- **THEN** the runner produces four equal-width sliding (train, OOS) pairs that together tile the slice with no overlap beyond the sliding-window structure

#### Scenario: Anchored fold derivation

- **WHEN** an experiment-spec declares `folds: {count: 4, scheme: anchored, gap: 0}` over the same slice
- **THEN** the runner produces four (train, OOS) pairs whose train segments share a common start and grow over time, while OOS segments slide

#### Scenario: Folds block uses absolute slice

- **WHEN** an experiment-spec has no top-level slice but the run template declares one
- **THEN** the runner derives folds against the run-template slice; if neither is present, validation fails
