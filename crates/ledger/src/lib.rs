//! SQLite-backed append-only experiment ledger.
//!
//! Tables (per spec `experiment-ledger`):
//! `runs`, `hypotheses`, `decisions`, `dataset_manifests`,
//! `divergence_warnings`, `objectives`, `strategy_versions`.
//!
//! Append-only enforcement uses BEFORE UPDATE / BEFORE DELETE triggers per
//! protected table so misbehaving clients see a structured `RAISE(ABORT, …)`
//! at the SQL boundary.
//!
//! Bulk per-bar arrays (trades, signals, equity, exec_log) live in JSON
//! sidecar files referenced by run id from the `runs` table. A parquet
//! upgrade is a planned follow-up; the `SidecarStore` API is shape-stable
//! across the swap.

pub mod error;
pub mod ledger;
pub mod queries;
pub mod records;
pub mod schema;
pub mod sidecar;

pub use error::LedgerError;
pub use ledger::Ledger;
pub use queries::RecentDecision;
pub use records::{
    DatasetManifestRecord, DecisionKind, DecisionRecord, DivergenceSeverity, DivergenceWarning,
    HypothesisRecord, ObjectiveRecord, RunRecord, StrategyVersionRecord,
};
pub use sidecar::{SidecarKind, SidecarStore};
