//! Read-only queries the Hypothesis Loop and other consumers run.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::records::{DecisionKind, HypothesisRecord};

/// One row joining a `decisions` row with the `hypotheses` row it references.
/// Returned by [`crate::Ledger::recent_decisions`].
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RecentDecision {
    pub decision_id: String,
    pub kind: DecisionKind,
    pub rationale: String,
    pub evidence: serde_json::Value,
    pub decided_at: DateTime<Utc>,
    pub hypothesis: HypothesisRecord,
}
