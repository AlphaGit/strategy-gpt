//! VXX volatility-range strategy — reference smoke strategy for the rewrite.
//!
//! Strategy summary
//! ----------------
//! VXX (the volatility-tracking ETN) tends to decay over time in contango,
//! the dominant regime where the VIX futures curve slopes upward. The
//! strategy holds a short VXX position during calm regimes (realized vol
//! below `vol_lo`) and flattens once realized vol crosses `vol_hi`, on the
//! assumption the contango bleed has paused or reversed. The thresholds are
//! configurable so the optimizer can sweep them.
//!
//! This is a *reference smoke strategy*: the loop's product is the
//! creation-and-test loop, not the strategy itself. We need a strategy that
//! compiles, trades, emits signals/decisions, and produces non-trivial
//! `BacktestResult` content so every downstream module exercises a realistic
//! input.
//!
//! Parameters (read at `on_init` from `state["__params__"]`):
//! - `vol_lo` (f64, default 0.01): enter short when realized vol ≤ vol_lo.
//! - `vol_hi` (f64, default 0.04): exit short when realized vol ≥ vol_hi.
//! - `size` (f64, default 100.0): short notional in VXX units.
//! - `symbol` (string, default "VXX").

use engine_rt::{
    strategy_entry, Bar, Context, Fill, Result, Sealed, Side, StateKey, Strategy, StrategyMeta,
};
use serde::{Deserialize, Serialize};
use serde_json::json;

const REALIZED_VOL_INDICATOR: &str = "realized_vol_20";

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct VxxParams {
    pub vol_lo: f64,
    pub vol_hi: f64,
    pub size: f64,
    pub symbol: String,
}

impl Default for VxxParams {
    fn default() -> Self {
        Self {
            vol_lo: 0.01,
            vol_hi: 0.04,
            size: 100.0,
            symbol: "VXX".to_string(),
        }
    }
}

#[derive(Default)]
pub struct VxxStrategy {
    params: VxxParams,
    /// Engine bar counter — used to skip the indicator's warm-up window so
    /// the first signal log carries a meaningful value.
    warmup_bars_seen: u32,
}

impl Sealed for VxxStrategy {}

impl Strategy for VxxStrategy {
    fn metadata(&self) -> StrategyMeta {
        StrategyMeta::new(
            "vxx_volatility_range",
            "0.1.0",
            "strategy-gpt",
            "Short-VXX-in-contango reference smoke strategy.",
        )
    }

    fn on_init(&mut self, ctx: &mut dyn Context) -> Result<()> {
        // Pull params from runtime state. `__params__` is the executor-seeded
        // mirror of `RunSpec.params`. Falls back to defaults when the key is
        // absent (lets tests run with `params: null`).
        let key = StateKey::from("__params__");
        if let Some(value) = ctx.state_get(&key)? {
            if !value.is_null() {
                if let Ok(p) = serde_json::from_value::<VxxParams>(value) {
                    self.params = p;
                }
            }
        }
        ctx.log_decision(
            "init",
            json!({
                "vol_lo": self.params.vol_lo,
                "vol_hi": self.params.vol_hi,
                "size": self.params.size,
                "symbol": self.params.symbol,
            }),
        );
        Ok(())
    }

    fn on_bar(&mut self, bar: &Bar, ctx: &mut dyn Context) -> Result<()> {
        if bar.symbol != self.params.symbol {
            return Ok(());
        }
        self.warmup_bars_seen = self.warmup_bars_seen.saturating_add(1);
        let vol = match ctx.read_indicator(&REALIZED_VOL_INDICATOR.to_string()) {
            Ok(v) => v,
            Err(_) => {
                // Indicator not warmed up yet — emit a suppressed signal so the
                // diagnostic record shows the warmup window explicitly.
                ctx.log_signal("vol_value", 0.0, false, Some("indicator_warmup"));
                return Ok(());
            }
        };
        ctx.log_signal("vol_value", vol, true, None);

        let position = ctx.get_position(&self.params.symbol);
        let is_short = position.size < 0.0;

        if !is_short && vol <= self.params.vol_lo {
            ctx.log_signal("enter_short", vol, true, None);
            ctx.submit_order(
                &self.params.symbol,
                Side::Short,
                self.params.size,
                None,
                None,
                Some("contango_low_vol_entry"),
            )?;
            return Ok(());
        }
        if is_short && vol >= self.params.vol_hi {
            ctx.log_signal("exit_short", vol, true, None);
            ctx.submit_order(
                &self.params.symbol,
                Side::Long,
                self.params.size,
                None,
                None,
                Some("backwardation_exit"),
            )?;
            return Ok(());
        }
        // Holding pattern.
        ctx.log_signal("hold", vol, false, Some("threshold_band"));
        Ok(())
    }

    fn on_fill(&mut self, _fill: &Fill, _ctx: &mut dyn Context) -> Result<()> {
        Ok(())
    }

    fn on_end(&mut self, ctx: &mut dyn Context) -> Result<()> {
        ctx.log_decision("end", json!({ "warmup_bars_seen": self.warmup_bars_seen }));
        Ok(())
    }
}

fn factory() -> Box<dyn Strategy> {
    Box::<VxxStrategy>::default()
}

strategy_entry!(factory);
