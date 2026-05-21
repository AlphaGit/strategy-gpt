# Spec: experiment-ledger

## Purpose

SQLite-backed append-only record of every run, hypothesis, decision, dataset manifest pin, and divergence warning. Together with the data cache, the ledger is sufficient to reproduce any past backtest byte-identically. Bulk per-bar arrays live in parquet sidecars referenced by run id.

## Requirements

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

### Requirement: Per-strategy storage layout

Hypothesis decisions, candidate source blobs, baseline caches, and LLM response blobs SHALL be partitioned per strategy under `ledger/strategies/<strategy_name>/`. Each per-strategy folder MUST contain `hypothesis_records.parquet`, `decision_records.parquet`, a `baseline/` subfolder for cached baseline results, a `sources/` subfolder for content-addressed candidate file sets, and a `responses/` subfolder for per-candidate LLM emission blobs. The strategy identity SHALL be the strategy crate name; cross-strategy queries iterate the directory listing.

#### Scenario: Two strategies have independent histories

- **WHEN** hypothesize is run against `vxx_volatility_range` and separately against `ema_crossover`
- **THEN** their decisions, sources, and responses are stored under `ledger/strategies/vxx_volatility_range/` and `ledger/strategies/ema_crossover/` respectively, with no shared records

#### Scenario: Per-candidate response blobs are co-located

- **WHEN** a candidate is processed through stages 1, 2, and 3 with one repair attempt at stage 3
- **THEN** the response blobs are stored at `ledger/strategies/<strategy_name>/responses/<decision_id>/{stage1_idea.md, stage2_commitments.md, stage3_files.md, repair_0.md}`

### Requirement: Content-addressed source-blob persistence

Candidate strategy source files SHALL be persisted under `ledger/strategies/<strategy_name>/sources/<files_set_hash>/` as a content-addressed bundle. The persisted `HypothesisRecord.proposed_change.files_manifest` MUST reference paths and blob hashes such that replay can reconstruct the candidate's working directory deterministically. Source bundles that already exist under their content hash MUST NOT be duplicated.

#### Scenario: Identical baseline source is deduplicated across candidates

- **WHEN** two candidates inherit identical baseline source files for several paths
- **THEN** those identical files share a single content-addressed blob and the candidates' `files_manifest` entries both reference the same hashes

#### Scenario: Replay reconstructs the files from blobs

- **WHEN** an operator requests replay of a recorded hypothesis decision
- **THEN** the ledger reads the referenced blobs from `sources/<files_set_hash>/` and reconstructs the candidate's working directory before invoking the build pipeline

### Requirement: Baseline-best cache per strategy

Each strategy folder SHALL maintain a `baseline/best.json` file caching the baseline-best result used as the comparison anchor for hypothesize verdicts. The cache MUST be populated either from a lookup against the `optimization_ledger` for the strategy's `(strategy_hash, dataset_manifest)` tuple or by computing a baseline optimize pass on demand when no prior optimize result exists. Subsequent hypothesize iterations on the same strategy and dataset_manifest MUST reuse the cached baseline-best.

#### Scenario: First hypothesize run computes baseline on demand

- **WHEN** the optimize-ledger has no entry for the strategy's dataset_manifest at hypothesize start
- **THEN** the ledger triggers a baseline optimize pass and writes the result to `baseline/best.json` before any candidate is evaluated

#### Scenario: Subsequent runs reuse the cache

- **WHEN** a second hypothesize run is launched against the same strategy and dataset_manifest
- **THEN** the loop loads `baseline/best.json` directly without re-running optimize
