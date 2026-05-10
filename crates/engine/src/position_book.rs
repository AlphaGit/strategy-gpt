//! Per-symbol position aggregation used during a backtest.
//!
//! Tracks current size and average entry price. Realized P&L is intentionally
//! NOT exposed to the running strategy — the engine emits it post-hoc as part
//! of the backtest result. We track it internally so post-hoc accounting is
//! cheap.

use engine_rt::{Fill, Position, Side};
use std::collections::HashMap;

#[derive(Clone, Debug, Default)]
pub struct PositionBook {
    positions: HashMap<String, InternalPosition>,
}

#[derive(Clone, Debug, Default)]
struct InternalPosition {
    /// Signed: positive long, negative short.
    size: f64,
    avg_price: f64,
    realized_pnl: f64,
}

impl PositionBook {
    pub fn new() -> Self {
        Self::default()
    }

    /// Snapshot of the position visible to a strategy. Strategies see size
    /// and average price; realized P&L is engine-only.
    pub fn position_view(&self, symbol: &str) -> Position {
        let p = self.positions.get(symbol).cloned().unwrap_or_default();
        Position {
            symbol: symbol.to_string(),
            size: p.size,
            avg_price: p.avg_price,
        }
    }

    pub fn realized_pnl(&self, symbol: &str) -> f64 {
        self.positions
            .get(symbol)
            .map(|p| p.realized_pnl)
            .unwrap_or(0.0)
    }

    pub fn unrealized_pnl(&self, symbol: &str, mark_price: f64) -> f64 {
        self.positions
            .get(symbol)
            .map(|p| (mark_price - p.avg_price) * p.size)
            .unwrap_or(0.0)
    }

    /// Apply a fill to the book. Handles open, add, partial close, full
    /// close, and reverse-through-zero in a single path. Fees reduce realized
    /// P&L.
    pub fn apply_fill(&mut self, fill: &Fill) {
        let entry = self.positions.entry(fill.symbol.clone()).or_default();

        // Convert side+size to signed delta.
        let signed_size = match fill.side {
            Side::Long => fill.size,
            Side::Short => -fill.size,
        };

        let old_size = entry.size;
        let new_size = old_size + signed_size;

        // Zero-cross or pure close: realize P&L on the closed portion.
        let same_direction = old_size.signum() == signed_size.signum() || old_size == 0.0;
        if same_direction {
            // Adding to existing exposure (or opening). Update avg_price by
            // size-weighted blend.
            if new_size == 0.0 {
                entry.avg_price = 0.0;
            } else {
                entry.avg_price =
                    (entry.avg_price * old_size + fill.price * signed_size) / new_size;
            }
        } else {
            // Closing or reversing.
            let closing_size = signed_size.abs().min(old_size.abs());
            // Realized P&L on the closed portion. For a long being closed by
            // a short: pnl = (fill.price - avg_price) * closing_size.
            let pnl_per_unit = if old_size > 0.0 {
                fill.price - entry.avg_price
            } else {
                entry.avg_price - fill.price
            };
            entry.realized_pnl += pnl_per_unit * closing_size;

            if new_size == 0.0 || (old_size > 0.0) == (new_size > 0.0) {
                // Full close to flat or partial close (still same direction).
                if new_size == 0.0 {
                    entry.avg_price = 0.0;
                }
                // avg_price unchanged on partial close.
            } else {
                // Reversed: opening a new position in the opposite direction
                // with the residual size at `fill.price`.
                entry.avg_price = fill.price;
            }
        }

        entry.size = new_size;
        entry.realized_pnl -= fill.fee;
    }
}
