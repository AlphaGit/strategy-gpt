use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DecisionEvent {
    pub ts: DateTime<Utc>,
    pub event: String,
    pub details: Value,
}
