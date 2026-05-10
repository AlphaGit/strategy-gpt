//! Fill model: how a submitted trade intent is converted to a simulated fill.
//!
//! Engine configuration, not strategy parameter.

use engine_rt::{Bar, Fill, Order, OrderId, Side};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum FillModel {
    /// Fill at the open of the bar that follows the bar in which the intent
    /// was submitted.
    NextBarOpen,
    /// Fill at the close of the bar in which the intent was submitted.
    /// Useful for end-of-day strategies where the decision and the fill share
    /// the closing print.
    CurrentBarClose,
}

impl Default for FillModel {
    fn default() -> Self {
        Self::NextBarOpen
    }
}

impl FillModel {
    /// Apply the fill model to a market intent (no limit/stop) against a bar.
    /// Returns the price at which the simulated fill executes, or `None` when
    /// the model decides the fill cannot occur on this bar.
    pub fn market_fill_price(self, bar: &Bar) -> f64 {
        match self {
            FillModel::NextBarOpen => bar.open,
            FillModel::CurrentBarClose => bar.close,
        }
    }

    /// Apply the fill model to a limit intent against a bar.
    /// Returns `Some(price)` when the limit price is reached on this bar
    /// according to the model's rules, otherwise `None`.
    pub fn limit_fill_price(self, order: &Order, bar: &Bar) -> Option<f64> {
        let limit = order.limit_price?;
        match (order.side, self) {
            // For a buy, the limit is satisfied when the bar trades at or
            // below it. The simulated fill price is the better of the two
            // (the limit, since fills do not cross).
            (Side::Long, FillModel::NextBarOpen) => {
                if bar.open <= limit {
                    Some(bar.open)
                } else if bar.low <= limit {
                    Some(limit)
                } else {
                    None
                }
            }
            (Side::Long, FillModel::CurrentBarClose) => {
                if bar.close <= limit {
                    Some(bar.close)
                } else if bar.low <= limit {
                    Some(limit)
                } else {
                    None
                }
            }
            (Side::Short, FillModel::NextBarOpen) => {
                if bar.open >= limit {
                    Some(bar.open)
                } else if bar.high >= limit {
                    Some(limit)
                } else {
                    None
                }
            }
            (Side::Short, FillModel::CurrentBarClose) => {
                if bar.close >= limit {
                    Some(bar.close)
                } else if bar.high >= limit {
                    Some(limit)
                } else {
                    None
                }
            }
        }
    }

    /// Build a [`Fill`] for an order that has been determined to fill at
    /// `price` against `bar`. `fee` is computed by the engine (slippage +
    /// commission); this helper packages the result.
    pub fn make_fill(order: &Order, price: f64, bar: &Bar, fee: f64) -> Fill {
        Fill {
            order_id: OrderId(order.id.0),
            symbol: order.symbol.clone(),
            side: order.side,
            size: order.size,
            price,
            fee,
            ts: bar.ts,
        }
    }
}
