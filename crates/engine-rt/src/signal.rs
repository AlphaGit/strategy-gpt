use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

pub type SignalName = String;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SignalEvent {
    pub name: SignalName,
    pub ts: DateTime<Utc>,
    pub value: f64,
    pub fired: bool,
    pub suppressed_by: Option<String>,
}
