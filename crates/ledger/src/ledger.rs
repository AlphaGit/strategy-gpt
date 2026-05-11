//! Append-only ledger implementation backed by SQLite.

use std::path::{Path, PathBuf};

use chrono::{DateTime, Utc};
use rusqlite::{params, Connection, OptionalExtension};

use crate::error::LedgerError;
use crate::queries::RecentDecision;
use crate::records::{
    DatasetManifestRecord, DecisionKind, DecisionRecord, DivergenceWarning, HypothesisRecord,
    ObjectiveRecord, RunRecord, StrategyVersionRecord,
};
use crate::schema::{CREATE_SCHEMA_SQL, SCHEMA_VERSION};
use crate::sidecar::SidecarStore;

#[derive(Debug)]
pub struct Ledger {
    conn: Connection,
    root: PathBuf,
    sidecars: SidecarStore,
}

impl Ledger {
    /// Open a ledger rooted at `path`. The directory is created if missing.
    /// The SQLite file lives at `<path>/ledger.sqlite`; sidecars live under
    /// `<path>/sidecars/`.
    pub fn open(path: impl AsRef<Path>) -> Result<Self, LedgerError> {
        let root = path.as_ref().to_path_buf();
        std::fs::create_dir_all(&root)?;
        let db_path = root.join("ledger.sqlite");
        let conn = Connection::open(&db_path)?;
        conn.pragma_update(None, "foreign_keys", "ON")?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.execute_batch(CREATE_SCHEMA_SQL)?;
        ensure_schema_version(&conn)?;
        let sidecars = SidecarStore::new(root.join("sidecars"));
        std::fs::create_dir_all(sidecars.root())?;
        Ok(Self {
            conn,
            root,
            sidecars,
        })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn sidecars(&self) -> &SidecarStore {
        &self.sidecars
    }

    pub fn record_run(&self, r: &RunRecord) -> Result<(), LedgerError> {
        self.conn.execute(
            "INSERT INTO runs (id, strategy_artifact, dataset_manifest_hash, hypothesis_id,
                               parameters_json, modes_json, seed, runner_version,
                               slice_json, engine_config_json, parallelism,
                               verdict_json, metrics_json, sidecar_root, created_at)
             VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15)",
            params![
                r.id,
                r.strategy_artifact,
                r.dataset_manifest_hash,
                r.hypothesis_id,
                serde_json::to_string(&r.parameters)?,
                serde_json::to_string(&r.modes)?,
                r.seed as i64,
                format!("{}", r.runner_version),
                serde_json::to_string(&r.slice)?,
                serde_json::to_string(&r.engine_config)?,
                r.parallelism as i64,
                r.verdict.as_ref().map(serde_json::to_string).transpose()?,
                r.metrics.as_ref().map(serde_json::to_string).transpose()?,
                r.sidecar_root,
                r.created_at.to_rfc3339(),
            ],
        )?;
        Ok(())
    }

    pub fn record_hypothesis(&self, h: &HypothesisRecord) -> Result<(), LedgerError> {
        self.conn.execute(
            "INSERT INTO hypotheses (id, name, target_metric, falsification_json,
                                     proposed_change_json, kb_cites_json, created_at)
             VALUES (?1,?2,?3,?4,?5,?6,?7)",
            params![
                h.id,
                h.name,
                h.target_metric,
                serde_json::to_string(&h.falsification)?,
                serde_json::to_string(&h.proposed_change)?,
                serde_json::to_string(&h.kb_cites)?,
                h.created_at.to_rfc3339(),
            ],
        )?;
        Ok(())
    }

    pub fn record_decision(&self, d: &DecisionRecord) -> Result<(), LedgerError> {
        self.conn.execute(
            "INSERT INTO decisions (id, hypothesis_id, kind, rationale, evidence_json, decided_at)
             VALUES (?1,?2,?3,?4,?5,?6)",
            params![
                d.id,
                d.hypothesis_id,
                d.kind.as_str(),
                d.rationale,
                serde_json::to_string(&d.evidence)?,
                d.decided_at.to_rfc3339(),
            ],
        )?;
        Ok(())
    }

    pub fn record_dataset_manifest(&self, m: &DatasetManifestRecord) -> Result<(), LedgerError> {
        self.conn.execute(
            "INSERT INTO dataset_manifests (hash, manifest_json, created_at)
             VALUES (?1,?2,?3)",
            params![
                m.hash,
                serde_json::to_string(&m.manifest)?,
                m.created_at.to_rfc3339(),
            ],
        )?;
        Ok(())
    }

    pub fn record_divergence(&self, w: &DivergenceWarning) -> Result<(), LedgerError> {
        self.conn.execute(
            "INSERT INTO divergence_warnings
                (symbol, ts, providers, values_json, reason, severity, logged_at)
             VALUES (?1,?2,?3,?4,?5,?6,?7)",
            params![
                w.symbol,
                w.ts.to_rfc3339(),
                serde_json::to_string(&w.providers)?,
                serde_json::to_string(&w.values)?,
                w.reason,
                w.severity.as_str(),
                w.logged_at.to_rfc3339(),
            ],
        )?;
        Ok(())
    }

    pub fn record_objective(&self, o: &ObjectiveRecord) -> Result<(), LedgerError> {
        self.conn.execute(
            "INSERT INTO objectives (strategy_id, spec_json, created_at)
             VALUES (?1,?2,?3)",
            params![
                o.strategy_id,
                serde_json::to_string(&o.spec)?,
                o.created_at.to_rfc3339(),
            ],
        )?;
        Ok(())
    }

    pub fn record_strategy_version(&self, v: &StrategyVersionRecord) -> Result<(), LedgerError> {
        self.conn.execute(
            "INSERT INTO strategy_versions (artifact_hash, runner_version, metadata_json, created_at)
             VALUES (?1,?2,?3,?4)",
            params![
                v.artifact_hash,
                format!("{}", v.runner_version),
                serde_json::to_string(&v.metadata)?,
                v.created_at.to_rfc3339(),
            ],
        )?;
        Ok(())
    }

    /// Hypothesis Loop entry point: return the most recent `n` decisions
    /// joined with their hypotheses, ordered newest first.
    pub fn recent_decisions(&self, n: usize) -> Result<Vec<RecentDecision>, LedgerError> {
        let mut stmt = self.conn.prepare(
            "SELECT d.id, d.kind, d.rationale, d.evidence_json, d.decided_at,
                    h.id, h.name, h.target_metric, h.falsification_json,
                    h.proposed_change_json, h.kb_cites_json, h.created_at
             FROM decisions d
             JOIN hypotheses h ON h.id = d.hypothesis_id
             ORDER BY d.decided_at DESC
             LIMIT ?1",
        )?;
        let rows = stmt.query_map(params![n as i64], |row| {
            let decision_id: String = row.get(0)?;
            let kind_str: String = row.get(1)?;
            let rationale: String = row.get(2)?;
            let evidence_str: String = row.get(3)?;
            let decided_at_str: String = row.get(4)?;
            let hypothesis_id: String = row.get(5)?;
            let name: String = row.get(6)?;
            let target_metric: String = row.get(7)?;
            let falsification_str: String = row.get(8)?;
            let proposed_change_str: String = row.get(9)?;
            let kb_cites_str: String = row.get(10)?;
            let h_created_at_str: String = row.get(11)?;
            Ok(RawRecent {
                decision_id,
                kind_str,
                rationale,
                evidence_str,
                decided_at_str,
                hypothesis_id,
                name,
                target_metric,
                falsification_str,
                proposed_change_str,
                kb_cites_str,
                h_created_at_str,
            })
        })?;
        let mut out = Vec::with_capacity(n);
        for r in rows {
            let r = r?;
            let kind = DecisionKind::parse(&r.kind_str).ok_or_else(|| {
                LedgerError::Schema(format!("unknown decision.kind `{}`", r.kind_str))
            })?;
            out.push(RecentDecision {
                decision_id: r.decision_id,
                kind,
                rationale: r.rationale,
                evidence: serde_json::from_str(&r.evidence_str)?,
                decided_at: parse_ts(&r.decided_at_str)?,
                hypothesis: HypothesisRecord {
                    id: r.hypothesis_id,
                    name: r.name,
                    target_metric: r.target_metric,
                    falsification: serde_json::from_str(&r.falsification_str)?,
                    proposed_change: serde_json::from_str(&r.proposed_change_str)?,
                    kb_cites: serde_json::from_str(&r.kb_cites_str)?,
                    created_at: parse_ts(&r.h_created_at_str)?,
                },
            });
        }
        Ok(out)
    }

    /// Look up one run by id.
    pub fn get_run(&self, id: &str) -> Result<Option<RunRecord>, LedgerError> {
        let mut stmt = self.conn.prepare(
            "SELECT id, strategy_artifact, dataset_manifest_hash, hypothesis_id,
                    parameters_json, modes_json, seed, runner_version,
                    slice_json, engine_config_json, parallelism,
                    verdict_json, metrics_json, sidecar_root, created_at
             FROM runs WHERE id = ?1",
        )?;
        let row = stmt
            .query_row(params![id], |row| {
                Ok(RawRun {
                    id: row.get(0)?,
                    strategy_artifact: row.get(1)?,
                    dataset_manifest_hash: row.get(2)?,
                    hypothesis_id: row.get(3)?,
                    parameters_json: row.get(4)?,
                    modes_json: row.get(5)?,
                    seed: row.get(6)?,
                    runner_version_str: row.get(7)?,
                    slice_json: row.get(8)?,
                    engine_config_json: row.get(9)?,
                    parallelism: row.get(10)?,
                    verdict_json: row.get(11)?,
                    metrics_json: row.get(12)?,
                    sidecar_root: row.get(13)?,
                    created_at_str: row.get(14)?,
                })
            })
            .optional()?;
        let Some(r) = row else { return Ok(None) };
        Ok(Some(RunRecord {
            id: r.id,
            strategy_artifact: r.strategy_artifact,
            dataset_manifest_hash: r.dataset_manifest_hash,
            hypothesis_id: r.hypothesis_id,
            parameters: serde_json::from_str(&r.parameters_json)?,
            modes: serde_json::from_str(&r.modes_json)?,
            seed: r.seed as u64,
            runner_version: parse_version(&r.runner_version_str)?,
            slice: serde_json::from_str(&r.slice_json)?,
            engine_config: serde_json::from_str(&r.engine_config_json)?,
            parallelism: r.parallelism as usize,
            verdict: opt_parse_json(r.verdict_json)?,
            metrics: opt_parse_json(r.metrics_json)?,
            sidecar_root: r.sidecar_root,
            created_at: parse_ts(&r.created_at_str)?,
        }))
    }

    /// Look up a previously recorded dataset manifest by its content hash.
    /// Required by the replay path (`spec::reproducibility-from-ledger-alone`).
    pub fn get_dataset_manifest(
        &self,
        hash: &str,
    ) -> Result<Option<DatasetManifestRecord>, LedgerError> {
        let mut stmt = self.conn.prepare(
            "SELECT hash, manifest_json, created_at
             FROM dataset_manifests WHERE hash = ?1",
        )?;
        let row = stmt
            .query_row(params![hash], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                ))
            })
            .optional()?;
        let Some((hash, manifest_str, created_str)) = row else {
            return Ok(None);
        };
        Ok(Some(DatasetManifestRecord {
            hash,
            manifest: serde_json::from_str(&manifest_str)?,
            created_at: parse_ts(&created_str)?,
        }))
    }
}

fn ensure_schema_version(conn: &Connection) -> Result<(), LedgerError> {
    let existing: Option<String> = conn
        .query_row(
            "SELECT value FROM meta WHERE key = 'schema_version'",
            [],
            |r| r.get(0),
        )
        .optional()?;
    match existing {
        None => {
            conn.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', ?1)",
                params![SCHEMA_VERSION.to_string()],
            )?;
            Ok(())
        }
        Some(v) => {
            let stored: i64 = v
                .parse()
                .map_err(|e| LedgerError::Schema(format!("schema_version unparseable: {e}")))?;
            if stored != SCHEMA_VERSION {
                return Err(LedgerError::Schema(format!(
                    "ledger schema_version {stored} does not match runtime {SCHEMA_VERSION}"
                )));
            }
            Ok(())
        }
    }
}

fn parse_ts(s: &str) -> Result<DateTime<Utc>, LedgerError> {
    DateTime::parse_from_rfc3339(s)
        .map(|t| t.with_timezone(&Utc))
        .map_err(|e| LedgerError::Schema(format!("invalid rfc3339 timestamp `{s}`: {e}")))
}

fn parse_version(s: &str) -> Result<engine_rt::RunnerVersion, LedgerError> {
    let parts: Vec<u16> = s
        .split('.')
        .map(|p| {
            p.parse::<u16>()
                .map_err(|e| LedgerError::Schema(format!("invalid version `{s}`: {e}")))
        })
        .collect::<Result<_, _>>()?;
    if parts.len() != 3 {
        return Err(LedgerError::Schema(format!(
            "version `{s}` must be major.minor.patch"
        )));
    }
    Ok(engine_rt::RunnerVersion::new(parts[0], parts[1], parts[2]))
}

fn opt_parse_json(s: Option<String>) -> Result<Option<serde_json::Value>, LedgerError> {
    match s {
        None => Ok(None),
        Some(s) => Ok(Some(serde_json::from_str(&s)?)),
    }
}

struct RawRecent {
    decision_id: String,
    kind_str: String,
    rationale: String,
    evidence_str: String,
    decided_at_str: String,
    hypothesis_id: String,
    name: String,
    target_metric: String,
    falsification_str: String,
    proposed_change_str: String,
    kb_cites_str: String,
    h_created_at_str: String,
}

struct RawRun {
    id: String,
    strategy_artifact: String,
    dataset_manifest_hash: String,
    hypothesis_id: Option<String>,
    parameters_json: String,
    modes_json: String,
    seed: i64,
    runner_version_str: String,
    slice_json: String,
    engine_config_json: String,
    parallelism: i64,
    verdict_json: Option<String>,
    metrics_json: Option<String>,
    sidecar_root: Option<String>,
    created_at_str: String,
}

/// Re-export, in case callers want their own divergence severity type.
pub use crate::records::DivergenceSeverity as Severity;
