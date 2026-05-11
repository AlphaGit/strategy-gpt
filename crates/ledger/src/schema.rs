//! SQL schema and append-only triggers.
//!
//! Bumping `SCHEMA_VERSION` requires either a backwards-additive migration
//! (new column with default, new table) or a fresh ledger file. Mutating
//! existing rows is forbidden by trigger; mutating the schema is allowed
//! through `ALTER TABLE`-additive only.

pub const SCHEMA_VERSION: i64 = 2;

pub const CREATE_SCHEMA_SQL: &str = r#"
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS runs (
    id                    TEXT PRIMARY KEY,
    strategy_artifact     TEXT NOT NULL,
    dataset_manifest_hash TEXT NOT NULL,
    hypothesis_id         TEXT,
    parameters_json       TEXT NOT NULL,
    modes_json            TEXT NOT NULL,
    seed                  INTEGER NOT NULL,
    runner_version        TEXT NOT NULL,
    slice_json            TEXT NOT NULL,
    engine_config_json    TEXT NOT NULL,
    parallelism           INTEGER NOT NULL,
    verdict_json          TEXT,
    metrics_json          TEXT,
    sidecar_root          TEXT,
    created_at            TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS hypotheses (
    id                       TEXT PRIMARY KEY,
    name                     TEXT NOT NULL,
    target_metric            TEXT NOT NULL,
    falsification_json       TEXT NOT NULL,
    proposed_change_json     TEXT NOT NULL,
    kb_cites_json            TEXT NOT NULL,
    created_at               TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS decisions (
    id            TEXT PRIMARY KEY,
    hypothesis_id TEXT NOT NULL,
    kind          TEXT NOT NULL,
    rationale     TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    decided_at    TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS dataset_manifests (
    hash          TEXT PRIMARY KEY,
    manifest_json TEXT NOT NULL,
    created_at    TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS divergence_warnings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    ts          TEXT NOT NULL,
    providers   TEXT NOT NULL,
    values_json TEXT NOT NULL,
    reason      TEXT NOT NULL,
    severity    TEXT NOT NULL,
    logged_at   TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS objectives (
    strategy_id TEXT PRIMARY KEY,
    spec_json   TEXT NOT NULL,
    created_at  TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS strategy_versions (
    artifact_hash  TEXT PRIMARY KEY,
    runner_version TEXT NOT NULL,
    metadata_json  TEXT NOT NULL,
    created_at     TEXT NOT NULL
) STRICT;

-- Append-only triggers. Each protected table rejects UPDATE and DELETE.
-- meta is intentionally NOT protected: schema_version updates allowed for
-- migrations.

CREATE TRIGGER IF NOT EXISTS no_update_runs       BEFORE UPDATE ON runs
BEGIN SELECT RAISE(ABORT, 'runs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_runs       BEFORE DELETE ON runs
BEGIN SELECT RAISE(ABORT, 'runs is append-only'); END;

CREATE TRIGGER IF NOT EXISTS no_update_hypotheses BEFORE UPDATE ON hypotheses
BEGIN SELECT RAISE(ABORT, 'hypotheses is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_hypotheses BEFORE DELETE ON hypotheses
BEGIN SELECT RAISE(ABORT, 'hypotheses is append-only'); END;

CREATE TRIGGER IF NOT EXISTS no_update_decisions  BEFORE UPDATE ON decisions
BEGIN SELECT RAISE(ABORT, 'decisions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_decisions  BEFORE DELETE ON decisions
BEGIN SELECT RAISE(ABORT, 'decisions is append-only'); END;

CREATE TRIGGER IF NOT EXISTS no_update_manifests  BEFORE UPDATE ON dataset_manifests
BEGIN SELECT RAISE(ABORT, 'dataset_manifests is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_manifests  BEFORE DELETE ON dataset_manifests
BEGIN SELECT RAISE(ABORT, 'dataset_manifests is append-only'); END;

CREATE TRIGGER IF NOT EXISTS no_update_warnings   BEFORE UPDATE ON divergence_warnings
BEGIN SELECT RAISE(ABORT, 'divergence_warnings is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_warnings   BEFORE DELETE ON divergence_warnings
BEGIN SELECT RAISE(ABORT, 'divergence_warnings is append-only'); END;

CREATE TRIGGER IF NOT EXISTS no_update_objectives BEFORE UPDATE ON objectives
BEGIN SELECT RAISE(ABORT, 'objectives is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_objectives BEFORE DELETE ON objectives
BEGIN SELECT RAISE(ABORT, 'objectives is append-only'); END;

CREATE TRIGGER IF NOT EXISTS no_update_strategies BEFORE UPDATE ON strategy_versions
BEGIN SELECT RAISE(ABORT, 'strategy_versions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_strategies BEFORE DELETE ON strategy_versions
BEGIN SELECT RAISE(ABORT, 'strategy_versions is append-only'); END;

CREATE INDEX IF NOT EXISTS idx_decisions_by_hypothesis ON decisions (hypothesis_id);
CREATE INDEX IF NOT EXISTS idx_decisions_by_time       ON decisions (decided_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_by_hypothesis      ON runs (hypothesis_id);
CREATE INDEX IF NOT EXISTS idx_runs_by_manifest        ON runs (dataset_manifest_hash);
CREATE INDEX IF NOT EXISTS idx_warnings_by_symbol      ON divergence_warnings (symbol, ts);
"#;
