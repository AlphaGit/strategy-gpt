## ADDED Requirements

### Requirement: SQLite-backed append-only record

The Experiment Ledger SHALL persist all experimental records in a single SQLite database. Records MUST be append-only; updates and deletes are not permitted on existing rows. Schema migrations are allowed via additive columns or new tables only.

#### Scenario: Updating an existing record is rejected

- **WHEN** any client attempts to UPDATE or DELETE on an existing row
- **THEN** the operation fails (either by trigger or by API constraint) and the original row remains intact

### Requirement: Ledger tables

The ledger SHALL include at minimum the following tables:

- `runs` — one row per backtest run with strategy artifact hash, dataset manifest hash, parameters, modes, seed, runner version, and verdict.
- `hypotheses` — one row per hypothesis with name, target metric, falsification criterion, proposed change, KB citations.
- `decisions` — one row per accept/reject decision with rationale, decision timestamp, and a reference to the related hypothesis.
- `dataset_manifests` — content-addressed manifest of cache blob hashes used for each run.
- `divergence_warnings` — one row per consolidation divergence with `(symbol, ts, providers, values, reason, severity)`.
- `objectives` — declarative objective specs per strategy.
- `strategy_versions` — strategy artifact metadata and runner-version mapping.

#### Scenario: Run record references manifest and hypothesis

- **WHEN** a backtest run completes
- **THEN** its `runs` row references its `dataset_manifests` row by hash and its originating `hypotheses` row by id

### Requirement: Parquet sidecars for bulk arrays

Trades, equity curves, signals, and `exec_log` SHALL be stored as parquet sidecar files referenced by run id from the SQLite `runs` table. The SQLite database MUST NOT contain bulk per-bar or per-tick arrays.

#### Scenario: Loading a run's trades

- **WHEN** a client requests trades for a run id
- **THEN** the ledger reads the parquet sidecar referenced by that run id and returns its contents

### Requirement: Decision log re-load for the hypothesis loop

The ledger SHALL expose a query that returns prior accepted and rejected hypotheses with rationale, ordered by recency. The Hypothesis Loop MUST be able to load this history into its workflow state on each invocation.

#### Scenario: Hypothesis loop reads recent decisions

- **WHEN** the Hypothesis Loop starts a new iteration
- **THEN** it queries the ledger for the most recent N decisions and includes them in the workflow's initial state

### Requirement: Reproducibility from ledger alone

Given a run id, the ledger together with the local cache SHALL be sufficient to reproduce a byte-identical `BacktestResult`.

#### Scenario: Replaying a run

- **WHEN** an operator replays a recorded run by id
- **THEN** the system reconstructs the `BatchSpec`, dataset, and seed from the ledger and produces identical results
