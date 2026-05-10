use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Serialize, Deserialize)]
pub enum Resolution {
    Minute,
    FiveMinute,
    FifteenMinute,
    Hour,
    Day,
    Week,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Bar {
    pub symbol: String,
    pub ts: DateTime<Utc>,
    pub resolution: Resolution,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}
