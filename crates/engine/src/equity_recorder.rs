//! Per-bar equity / drawdown / exposure recorder.

use chrono::{DateTime, Utc};

use crate::position_book::PositionBook;
use crate::result::EquityPoint;

pub struct EquityRecorder {
    initial_capital: f64,
    peak: f64,
    points: Vec<EquityPoint>,
}

impl EquityRecorder {
    pub fn new(initial_capital: f64) -> Self {
        Self {
            initial_capital,
            peak: initial_capital,
            points: Vec::new(),
        }
    }

    /// Record a bar. `realized_pnl` is the cumulative realized P&L across all
    /// closed trades so far (engine-internal, summed from
    /// [`PositionBook::realized_pnl`] across symbols). `mark_price_for` is a
    /// closure returning the per-symbol mark price for unrealized P&L.
    pub fn record(
        &mut self,
        ts: DateTime<Utc>,
        positions: &PositionBook,
        realized_pnl_total: f64,
        mark_price_for: impl Fn(&str) -> f64,
        symbols_held: &[String],
    ) {
        let mut unrealized = 0.0;
        let mut gross_exposure = 0.0;
        for sym in symbols_held {
            let pos = positions.position_view(sym);
            if pos.size == 0.0 {
                continue;
            }
            let mark = mark_price_for(sym);
            unrealized += positions.unrealized_pnl(sym, mark);
            gross_exposure += pos.size.abs() * mark;
        }
        let equity = self.initial_capital + realized_pnl_total + unrealized;
        if equity > self.peak {
            self.peak = equity;
        }
        let drawdown = if self.peak > 0.0 {
            ((self.peak - equity) / self.peak).max(0.0)
        } else {
            0.0
        };
        let exposure = if equity != 0.0 {
            gross_exposure / equity
        } else {
            0.0
        };
        self.points.push(EquityPoint {
            ts,
            equity,
            drawdown,
            exposure,
        });
    }

    pub fn into_points(self) -> Vec<EquityPoint> {
        self.points
    }

    pub fn peak(&self) -> f64 {
        self.peak
    }

    pub fn last_equity(&self) -> Option<f64> {
        self.points.last().map(|p| p.equity)
    }
}
