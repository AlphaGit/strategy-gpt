//! Concrete implementation of [`engine_rt::Context`] used by the worker
//! process driving a backtest.

use std::collections::HashMap;

use chrono::{DateTime, Utc};
use engine_rt::{
    Context, DecisionEvent, Error, IndicatorName, Order, OrderId, Position, Result, Side,
    SignalEvent, StateKey,
};
use serde_json::Value;

use crate::sanity::SanityBounds;
use crate::{indicators::IndicatorRegistry, intent::IntentBook, position_book::PositionBook};

/// Mutable state collected during one bar's worth of strategy execution.
///
/// The worker process owns one [`RuntimeContext`] per run and rebinds the
/// `now` timestamp on each bar before invoking strategy lifecycle methods.
pub struct RuntimeContext<'a> {
    pub now: DateTime<Utc>,
    pub intents: &'a mut IntentBook,
    pub positions: &'a mut PositionBook,
    pub indicators: &'a IndicatorRegistry,
    pub signals: &'a mut Vec<SignalEvent>,
    pub decisions: &'a mut Vec<DecisionEvent>,
    pub state: &'a mut HashMap<String, Value>,
    pub sanity: SanityBounds,
}

impl<'a> Context for RuntimeContext<'a> {
    fn submit_order(
        &mut self,
        symbol: &str,
        side: Side,
        size: f64,
        limit_price: Option<f64>,
        stop_price: Option<f64>,
        reason: Option<&str>,
    ) -> Result<OrderId> {
        if let Err(msg) = self.sanity.check_intent_size(size) {
            return Err(Error::RiskCap(msg));
        }
        let order = Order {
            id: OrderId(0), // assigned by IntentBook
            symbol: symbol.to_string(),
            side,
            size,
            limit_price,
            stop_price,
            submitted_at: self.now,
            reason: reason.map(str::to_string),
        };
        let id = self.intents.submit(order, self.now);
        Ok(id)
    }

    fn get_position(&self, symbol: &str) -> Position {
        self.positions.position_view(symbol)
    }

    fn log_signal(&mut self, name: &str, value: f64, fired: bool, suppressed_by: Option<&str>) {
        self.signals.push(SignalEvent {
            name: name.to_string(),
            ts: self.now,
            value,
            fired,
            suppressed_by: suppressed_by.map(str::to_string),
        });
    }

    fn log_decision(&mut self, event: &str, details: Value) {
        self.decisions.push(DecisionEvent {
            ts: self.now,
            event: event.to_string(),
            details,
        });
    }

    fn read_indicator(&self, name: &IndicatorName) -> Result<f64> {
        if !self.indicators.contains(name) {
            return Err(Error::UnknownIndicator(name.clone()));
        }
        self.indicators
            .value(name)
            .ok_or_else(|| Error::UnknownIndicator(format!("{name} (not warmed up)")))
    }

    fn state_get(&self, key: &StateKey) -> Result<Option<Value>> {
        Ok(self.state.get(&key.0).cloned())
    }

    fn state_set(&mut self, key: StateKey, value: Value) -> Result<()> {
        self.state.insert(key.0, value);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;
    use engine_rt::{Bar, Resolution};
    use serde_json::json;

    fn ts(y: i32, m: u32, d: u32) -> DateTime<Utc> {
        Utc.with_ymd_and_hms(y, m, d, 0, 0, 0).unwrap()
    }

    fn bar(symbol: &str, day: u32, close: f64) -> Bar {
        Bar {
            symbol: symbol.to_string(),
            ts: ts(2024, 1, day),
            resolution: Resolution::Day,
            open: close - 0.5,
            high: close + 1.0,
            low: close - 1.0,
            close,
            volume: 1_000.0,
        }
    }

    fn ctx_fixture<'a>(
        intents: &'a mut IntentBook,
        positions: &'a mut PositionBook,
        indicators: &'a IndicatorRegistry,
        signals: &'a mut Vec<SignalEvent>,
        decisions: &'a mut Vec<DecisionEvent>,
        state: &'a mut HashMap<String, Value>,
        now: DateTime<Utc>,
    ) -> RuntimeContext<'a> {
        RuntimeContext {
            now,
            intents,
            positions,
            indicators,
            signals,
            decisions,
            state,
            sanity: SanityBounds::default(),
        }
    }

    #[test]
    fn submit_order_routes_to_intent_book_and_assigns_id() {
        let mut intents = IntentBook::new();
        let mut positions = PositionBook::new();
        let indicators = IndicatorRegistry::new();
        let mut signals = Vec::new();
        let mut decisions = Vec::new();
        let mut state = HashMap::new();
        let mut ctx = ctx_fixture(
            &mut intents,
            &mut positions,
            &indicators,
            &mut signals,
            &mut decisions,
            &mut state,
            ts(2024, 1, 2),
        );

        let id_a = ctx
            .submit_order("VXX", Side::Long, 100.0, None, None, Some("entry"))
            .unwrap();
        let id_b = ctx
            .submit_order("VXX", Side::Short, 50.0, None, None, None)
            .unwrap();
        assert_ne!(id_a, id_b);
        assert_eq!(intents.count_pending(), 2);
    }

    #[test]
    fn submit_order_rejects_non_finite_or_zero_size() {
        let mut intents = IntentBook::new();
        let mut positions = PositionBook::new();
        let indicators = IndicatorRegistry::new();
        let mut signals = Vec::new();
        let mut decisions = Vec::new();
        let mut state = HashMap::new();
        let mut ctx = ctx_fixture(
            &mut intents,
            &mut positions,
            &indicators,
            &mut signals,
            &mut decisions,
            &mut state,
            ts(2024, 1, 2),
        );
        let err = ctx
            .submit_order("VXX", Side::Long, 0.0, None, None, None)
            .unwrap_err();
        assert!(matches!(err, Error::RiskCap(_)));
        let err2 = ctx
            .submit_order("VXX", Side::Long, f64::NAN, None, None, None)
            .unwrap_err();
        assert!(matches!(err2, Error::RiskCap(_)));
    }

    #[test]
    fn position_math_open_close_full_round_trip() {
        let mut book = PositionBook::new();
        // Open long 100 @ 50
        let order = Order {
            id: OrderId(1),
            symbol: "VXX".into(),
            side: Side::Long,
            size: 100.0,
            limit_price: None,
            stop_price: None,
            submitted_at: ts(2024, 1, 2),
            reason: None,
        };
        let fill = crate::fill_model::FillModel::make_fill(&order, 50.0, &bar("VXX", 2, 50.0), 0.0);
        book.apply_fill(&fill);
        let p = book.position_view("VXX");
        assert_eq!(p.size, 100.0);
        assert_eq!(p.avg_price, 50.0);

        // Close 100 @ 55 -> realized 500, flat
        let close = Order {
            id: OrderId(2),
            symbol: "VXX".into(),
            side: Side::Short,
            size: 100.0,
            limit_price: None,
            stop_price: None,
            submitted_at: ts(2024, 1, 3),
            reason: None,
        };
        let close_fill =
            crate::fill_model::FillModel::make_fill(&close, 55.0, &bar("VXX", 3, 55.0), 0.0);
        book.apply_fill(&close_fill);
        let p2 = book.position_view("VXX");
        assert_eq!(p2.size, 0.0);
        assert!((book.realized_pnl("VXX") - 500.0).abs() < 1e-9);
    }

    #[test]
    fn position_math_partial_close_preserves_avg_price() {
        let mut book = PositionBook::new();
        let open = Order {
            id: OrderId(1),
            symbol: "VXX".into(),
            side: Side::Long,
            size: 100.0,
            limit_price: None,
            stop_price: None,
            submitted_at: ts(2024, 1, 2),
            reason: None,
        };
        book.apply_fill(&crate::fill_model::FillModel::make_fill(
            &open,
            50.0,
            &bar("VXX", 2, 50.0),
            0.0,
        ));
        // Close 40 @ 55 -> realized 200; remaining size 60 still at avg 50
        let close = Order {
            id: OrderId(2),
            symbol: "VXX".into(),
            side: Side::Short,
            size: 40.0,
            limit_price: None,
            stop_price: None,
            submitted_at: ts(2024, 1, 3),
            reason: None,
        };
        book.apply_fill(&crate::fill_model::FillModel::make_fill(
            &close,
            55.0,
            &bar("VXX", 3, 55.0),
            0.0,
        ));
        let p = book.position_view("VXX");
        assert_eq!(p.size, 60.0);
        assert_eq!(p.avg_price, 50.0);
        assert!((book.realized_pnl("VXX") - 200.0).abs() < 1e-9);
    }

    #[test]
    fn position_math_reverse_through_zero_resets_avg_price() {
        let mut book = PositionBook::new();
        let open = Order {
            id: OrderId(1),
            symbol: "VXX".into(),
            side: Side::Long,
            size: 100.0,
            limit_price: None,
            stop_price: None,
            submitted_at: ts(2024, 1, 2),
            reason: None,
        };
        book.apply_fill(&crate::fill_model::FillModel::make_fill(
            &open,
            50.0,
            &bar("VXX", 2, 50.0),
            0.0,
        ));
        // Sell 150 @ 55 -> closes 100 long (realized 500), opens 50 short @ 55
        let reverse = Order {
            id: OrderId(2),
            symbol: "VXX".into(),
            side: Side::Short,
            size: 150.0,
            limit_price: None,
            stop_price: None,
            submitted_at: ts(2024, 1, 3),
            reason: None,
        };
        book.apply_fill(&crate::fill_model::FillModel::make_fill(
            &reverse,
            55.0,
            &bar("VXX", 3, 55.0),
            0.0,
        ));
        let p = book.position_view("VXX");
        assert_eq!(p.size, -50.0);
        assert_eq!(p.avg_price, 55.0);
        assert!((book.realized_pnl("VXX") - 500.0).abs() < 1e-9);
    }

    #[test]
    fn signal_logging_records_event_with_now_timestamp() {
        let mut intents = IntentBook::new();
        let mut positions = PositionBook::new();
        let indicators = IndicatorRegistry::new();
        let mut signals = Vec::new();
        let mut decisions = Vec::new();
        let mut state = HashMap::new();
        let now = ts(2024, 1, 2);
        let mut ctx = ctx_fixture(
            &mut intents,
            &mut positions,
            &indicators,
            &mut signals,
            &mut decisions,
            &mut state,
            now,
        );
        ctx.log_signal("vol_spike", 0.42, true, None);
        ctx.log_signal("trend_filter", 0.0, false, Some("regime_filter"));
        assert_eq!(signals.len(), 2);
        assert_eq!(signals[0].name, "vol_spike");
        assert_eq!(signals[0].ts, now);
        assert!(signals[0].fired);
        assert_eq!(signals[1].suppressed_by.as_deref(), Some("regime_filter"));
        assert!(!signals[1].fired);
    }

    #[test]
    fn decision_logging_captures_payload() {
        let mut intents = IntentBook::new();
        let mut positions = PositionBook::new();
        let indicators = IndicatorRegistry::new();
        let mut signals = Vec::new();
        let mut decisions = Vec::new();
        let mut state = HashMap::new();
        let mut ctx = ctx_fixture(
            &mut intents,
            &mut positions,
            &indicators,
            &mut signals,
            &mut decisions,
            &mut state,
            ts(2024, 1, 2),
        );
        ctx.log_decision("hedge_skipped", json!({ "reason": "cap_reached" }));
        assert_eq!(decisions.len(), 1);
        assert_eq!(decisions[0].event, "hedge_skipped");
        assert_eq!(decisions[0].details["reason"], "cap_reached");
    }

    #[test]
    fn state_round_trip_preserves_value() {
        let mut intents = IntentBook::new();
        let mut positions = PositionBook::new();
        let indicators = IndicatorRegistry::new();
        let mut signals = Vec::new();
        let mut decisions = Vec::new();
        let mut state = HashMap::new();
        let mut ctx = ctx_fixture(
            &mut intents,
            &mut positions,
            &indicators,
            &mut signals,
            &mut decisions,
            &mut state,
            ts(2024, 1, 2),
        );
        let key: StateKey = "last_entry_ts".into();
        ctx.state_set(key.clone(), json!("2024-01-02")).unwrap();
        let v = ctx.state_get(&key).unwrap();
        assert_eq!(v, Some(json!("2024-01-02")));
        let missing = ctx.state_get(&"absent".into()).unwrap();
        assert_eq!(missing, None);
    }

    #[test]
    fn read_indicator_unknown_errors() {
        let mut intents = IntentBook::new();
        let mut positions = PositionBook::new();
        let indicators = IndicatorRegistry::new();
        let mut signals = Vec::new();
        let mut decisions = Vec::new();
        let mut state = HashMap::new();
        let ctx = ctx_fixture(
            &mut intents,
            &mut positions,
            &indicators,
            &mut signals,
            &mut decisions,
            &mut state,
            ts(2024, 1, 2),
        );
        let err = ctx.read_indicator(&"sma_20".to_string()).unwrap_err();
        assert!(matches!(err, Error::UnknownIndicator(_)));
    }

    #[test]
    fn read_indicator_returns_value_after_warmup() {
        use crate::indicators::baseline_registry;
        let mut intents = IntentBook::new();
        let mut positions = PositionBook::new();
        let mut indicators = baseline_registry();
        // Feed 25 bars with linearly rising close so SMA stabilizes.
        for d in 1..=25 {
            indicators.update_all(&Bar {
                symbol: "VXX".into(),
                ts: ts(2024, 1, d),
                resolution: Resolution::Day,
                open: d as f64,
                high: d as f64 + 0.5,
                low: d as f64 - 0.5,
                close: d as f64,
                volume: 1.0,
            });
        }
        let mut signals = Vec::new();
        let mut decisions = Vec::new();
        let mut state = HashMap::new();
        let ctx = ctx_fixture(
            &mut intents,
            &mut positions,
            &indicators,
            &mut signals,
            &mut decisions,
            &mut state,
            ts(2024, 1, 26),
        );
        let v = ctx.read_indicator(&"sma_20".to_string()).unwrap();
        // SMA(20) of a linearly rising sequence ending at 25 = average of 6..=25 = 15.5
        assert!((v - 15.5).abs() < 1e-9);
    }
}
