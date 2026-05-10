//! Closed-trade detection.
//!
//! Watches simulated fills, tracks the open trade per symbol, and emits a
//! [`Trade`] when the position closes (or partially closes). Reverse-through-
//! zero is handled as a close + open pair.

use std::collections::HashMap;

use chrono::{DateTime, Utc};
use engine_rt::{Fill, Side};

use crate::result::Trade;

#[derive(Clone, Debug)]
struct OpenTrade {
    entry_ts: DateTime<Utc>,
    side: Side,
    size: f64,
    entry_price: f64,
    fees_in: f64,
    reason_in: Option<String>,
    signals_at_entry: Vec<String>,
}

#[derive(Default)]
pub struct TradeLog {
    open: HashMap<String, OpenTrade>,
    closed: Vec<Trade>,
}

impl TradeLog {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn closed(&self) -> &[Trade] {
        &self.closed
    }

    pub fn into_closed(self) -> Vec<Trade> {
        self.closed
    }

    pub fn open_count(&self) -> usize {
        self.open.len()
    }

    /// Record a fill. `reason` is taken from the originating order (the engine
    /// passes `Order::reason` through). `signals_at_entry` is captured at fill
    /// time so the close record can reference what the strategy was looking at.
    pub fn record_fill(&mut self, fill: &Fill, reason: Option<&str>, signals_active: &[String]) {
        let key = fill.symbol.clone();
        let open = self.open.remove(&key);
        match open {
            None => {
                self.open.insert(
                    key,
                    OpenTrade {
                        entry_ts: fill.ts,
                        side: fill.side,
                        size: fill.size,
                        entry_price: fill.price,
                        fees_in: fill.fee,
                        reason_in: reason.map(str::to_string),
                        signals_at_entry: signals_active.to_vec(),
                    },
                );
            }
            Some(open) if open.side == fill.side => {
                // Add to existing position; weighted average entry price.
                let total_size = open.size + fill.size;
                let blended = (open.entry_price * open.size + fill.price * fill.size) / total_size;
                self.open.insert(
                    key,
                    OpenTrade {
                        entry_ts: open.entry_ts,
                        side: open.side,
                        size: total_size,
                        entry_price: blended,
                        fees_in: open.fees_in + fill.fee,
                        reason_in: open.reason_in,
                        signals_at_entry: open.signals_at_entry,
                    },
                );
            }
            Some(open) => {
                // Opposite direction.
                let close_size = fill.size.min(open.size);
                let pnl_per_unit = match open.side {
                    Side::Long => fill.price - open.entry_price,
                    Side::Short => open.entry_price - fill.price,
                };
                // Fees attributable to the closed portion: full entry fees +
                // pro-rated exit fee.
                let exit_fee_share = if fill.size > 0.0 {
                    fill.fee * (close_size / fill.size)
                } else {
                    0.0
                };
                let trade = Trade {
                    entry_ts: open.entry_ts,
                    exit_ts: fill.ts,
                    symbol: fill.symbol.clone(),
                    side: open.side,
                    size: close_size,
                    entry_price: open.entry_price,
                    exit_price: fill.price,
                    pnl: pnl_per_unit * close_size - open.fees_in - exit_fee_share,
                    fees: open.fees_in + exit_fee_share,
                    reason_in: open.reason_in.clone(),
                    reason_out: reason.map(str::to_string),
                    signals_at_entry: open.signals_at_entry.clone(),
                };
                self.closed.push(trade);

                if fill.size > open.size {
                    // Reverse-through-zero: residual opens a new trade in the
                    // opposite direction at this fill's price.
                    let residual = fill.size - open.size;
                    let residual_fee = if fill.size > 0.0 {
                        fill.fee * (residual / fill.size)
                    } else {
                        0.0
                    };
                    self.open.insert(
                        key,
                        OpenTrade {
                            entry_ts: fill.ts,
                            side: fill.side,
                            size: residual,
                            entry_price: fill.price,
                            fees_in: residual_fee,
                            reason_in: reason.map(str::to_string),
                            signals_at_entry: signals_active.to_vec(),
                        },
                    );
                } else if fill.size < open.size {
                    // Partial close: reduce open size, retain entry price.
                    self.open.insert(
                        key,
                        OpenTrade {
                            entry_ts: open.entry_ts,
                            side: open.side,
                            size: open.size - close_size,
                            entry_price: open.entry_price,
                            // Pro-rate retained entry fees.
                            fees_in: open.fees_in * ((open.size - close_size) / open.size),
                            reason_in: open.reason_in,
                            signals_at_entry: open.signals_at_entry,
                        },
                    );
                }
                // If exactly equal, position is flat; nothing to reinsert.
            }
        }
    }

    /// At end of run, close any still-open trades against `mark_price` for
    /// each symbol. `marks` maps symbol -> (last_ts, last_price).
    pub fn close_remaining(&mut self, marks: &HashMap<String, (DateTime<Utc>, f64)>) {
        let symbols: Vec<String> = self.open.keys().cloned().collect();
        for symbol in symbols {
            let Some(open) = self.open.remove(&symbol) else {
                continue;
            };
            let (ts, price) = marks
                .get(&symbol)
                .copied()
                .unwrap_or((open.entry_ts, open.entry_price));
            let pnl_per_unit = match open.side {
                Side::Long => price - open.entry_price,
                Side::Short => open.entry_price - price,
            };
            self.closed.push(Trade {
                entry_ts: open.entry_ts,
                exit_ts: ts,
                symbol,
                side: open.side,
                size: open.size,
                entry_price: open.entry_price,
                exit_price: price,
                pnl: pnl_per_unit * open.size - open.fees_in,
                fees: open.fees_in,
                reason_in: open.reason_in,
                reason_out: Some("end_of_run".into()),
                signals_at_entry: open.signals_at_entry,
            });
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;
    use engine_rt::{Fill, OrderId};

    fn ts(d: u32) -> DateTime<Utc> {
        Utc.with_ymd_and_hms(2024, 1, d, 0, 0, 0).unwrap()
    }

    fn fill(day: u32, side: Side, size: f64, price: f64) -> Fill {
        Fill {
            order_id: OrderId(day as u64),
            symbol: "VXX".into(),
            side,
            size,
            price,
            fee: 0.0,
            ts: ts(day),
        }
    }

    #[test]
    fn open_then_close_emits_one_trade_with_pnl() {
        let mut log = TradeLog::new();
        log.record_fill(&fill(2, Side::Long, 100.0, 50.0), Some("entry"), &[]);
        assert_eq!(log.closed().len(), 0);
        assert_eq!(log.open_count(), 1);
        log.record_fill(&fill(3, Side::Short, 100.0, 55.0), Some("exit"), &[]);
        assert_eq!(log.closed().len(), 1);
        assert_eq!(log.open_count(), 0);
        let t = &log.closed()[0];
        assert_eq!(t.size, 100.0);
        assert!((t.pnl - 500.0).abs() < 1e-9);
        assert_eq!(t.reason_in.as_deref(), Some("entry"));
        assert_eq!(t.reason_out.as_deref(), Some("exit"));
    }

    #[test]
    fn partial_close_keeps_remaining_open() {
        let mut log = TradeLog::new();
        log.record_fill(&fill(2, Side::Long, 100.0, 50.0), None, &[]);
        log.record_fill(&fill(3, Side::Short, 40.0, 55.0), None, &[]);
        assert_eq!(log.closed().len(), 1);
        assert_eq!(log.closed()[0].size, 40.0);
        assert!((log.closed()[0].pnl - 200.0).abs() < 1e-9);
        assert_eq!(log.open_count(), 1);
    }

    #[test]
    fn reverse_through_zero_emits_close_and_opens_short() {
        let mut log = TradeLog::new();
        log.record_fill(&fill(2, Side::Long, 100.0, 50.0), Some("long_entry"), &[]);
        log.record_fill(
            &fill(3, Side::Short, 150.0, 55.0),
            Some("flip"),
            &["regime_change".to_string()],
        );
        assert_eq!(log.closed().len(), 1);
        let closed = &log.closed()[0];
        assert_eq!(closed.size, 100.0);
        assert!((closed.pnl - 500.0).abs() < 1e-9);
        assert_eq!(log.open_count(), 1);
    }

    #[test]
    fn end_of_run_closes_open_trade_against_mark() {
        let mut log = TradeLog::new();
        log.record_fill(&fill(2, Side::Long, 100.0, 50.0), None, &[]);
        let mut marks = HashMap::new();
        marks.insert("VXX".to_string(), (ts(10), 60.0));
        log.close_remaining(&marks);
        assert_eq!(log.closed().len(), 1);
        let t = &log.closed()[0];
        assert_eq!(t.size, 100.0);
        assert!((t.pnl - 1000.0).abs() < 1e-9);
        assert_eq!(t.reason_out.as_deref(), Some("end_of_run"));
    }

    #[test]
    fn signals_at_entry_recorded_on_open() {
        let mut log = TradeLog::new();
        log.record_fill(
            &fill(2, Side::Long, 100.0, 50.0),
            Some("entry"),
            &["vol_spike".into()],
        );
        log.record_fill(&fill(3, Side::Short, 100.0, 55.0), None, &[]);
        assert_eq!(
            log.closed()[0].signals_at_entry,
            vec!["vol_spike".to_string()]
        );
    }
}
