## ADDED Requirements

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
