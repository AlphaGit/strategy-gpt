//! Engine-provided indicator registry.
//!
//! Strategies do not implement indicators directly; the engine maintains a
//! registry of named indicators and updates them on every bar. Strategies
//! query the latest value via `Context::read_indicator(name)`.

mod atr;
mod ema;
mod realized_vol;
mod rsi;
mod sma;

use engine_rt::Bar;
use std::collections::HashMap;

pub use atr::Atr;
pub use ema::Ema;
pub use realized_vol::RealizedVol;
pub use rsi::Rsi;
pub use sma::Sma;

/// Stateful indicator updated bar-by-bar.
pub trait Indicator: Send {
    /// Stable name used by `Context::read_indicator`.
    fn name(&self) -> &str;
    /// Feed the next bar into the indicator. Indicators MAY ignore the high,
    /// low, volume fields if irrelevant.
    fn update(&mut self, bar: &Bar);
    /// Latest computed value, or `None` if not yet warmed up.
    fn value(&self) -> Option<f64>;
}

/// Registry holds named indicators and routes `update` calls to all of them
/// on each bar.
#[derive(Default)]
pub struct IndicatorRegistry {
    inner: HashMap<String, Box<dyn Indicator>>,
}

impl IndicatorRegistry {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn register<I: Indicator + 'static>(&mut self, indicator: I) {
        self.inner
            .insert(indicator.name().to_string(), Box::new(indicator));
    }

    pub fn update_all(&mut self, bar: &Bar) {
        for ind in self.inner.values_mut() {
            ind.update(bar);
        }
    }

    pub fn value(&self, name: &str) -> Option<f64> {
        self.inner.get(name).and_then(|i| i.value())
    }

    pub fn contains(&self, name: &str) -> bool {
        self.inner.contains_key(name)
    }
}

/// Baseline registry with the indicators called for in the rewrite spec:
/// SMA(20), EMA(20), RSI(14), ATR(14), realized_vol(20).
///
/// Names follow the convention `<kind>_<period>`. Period choices are
/// conventional defaults; strategies can register additional periods at
/// init time.
pub fn baseline_registry() -> IndicatorRegistry {
    let mut r = IndicatorRegistry::new();
    r.register(Sma::new("sma_20", 20));
    r.register(Ema::new("ema_20", 20));
    r.register(Rsi::new("rsi_14", 14));
    r.register(Atr::new("atr_14", 14));
    r.register(RealizedVol::new("realized_vol_20", 20));
    r
}
