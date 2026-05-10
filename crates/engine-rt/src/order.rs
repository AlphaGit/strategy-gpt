use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Serialize, Deserialize)]
pub enum Side {
    Long,
    Short,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Serialize, Deserialize)]
pub struct OrderId(pub u64);

/// Trade intent submitted by a strategy during a backtest.
///
/// "Order" here is research-loop terminology for "I want this position change
/// applied". The engine simulates the fill internally; there is no live order
/// book and no cancellation pathway.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Order {
    pub id: OrderId,
    pub symbol: String,
    pub side: Side,
    pub size: f64,
    pub limit_price: Option<f64>,
    pub stop_price: Option<f64>,
    pub submitted_at: DateTime<Utc>,
    pub reason: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Fill {
    pub order_id: OrderId,
    pub symbol: String,
    pub side: Side,
    pub size: f64,
    pub price: f64,
    pub fee: f64,
    pub ts: DateTime<Utc>,
}

/// Accounting view of a strategy's current position used for backtest
/// decisions. Realized/unrealized P&L are produced by the engine post-hoc as
/// part of the run output — they are not part of the strategy's runtime view.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Position {
    pub symbol: String,
    pub size: f64,
    pub avg_price: f64,
}
