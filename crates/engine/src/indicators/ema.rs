use engine_rt::Bar;

use super::Indicator;

/// Exponential moving average over close prices, smoothing factor
/// `alpha = 2 / (period + 1)`. Warm-up uses an arithmetic mean over the first
/// `period` bars; subsequent bars use the recursive EMA update.
#[derive(Clone, Debug)]
pub struct Ema {
    name: String,
    period: usize,
    alpha: f64,
    seen: usize,
    sum_for_seed: f64,
    value: Option<f64>,
}

impl Ema {
    pub fn new(name: impl Into<String>, period: usize) -> Self {
        assert!(period > 0, "EMA period must be positive");
        let alpha = 2.0 / (period as f64 + 1.0);
        Self {
            name: name.into(),
            period,
            alpha,
            seen: 0,
            sum_for_seed: 0.0,
            value: None,
        }
    }
}

impl Indicator for Ema {
    fn name(&self) -> &str {
        &self.name
    }

    fn update(&mut self, bar: &Bar) {
        self.seen += 1;
        if self.seen <= self.period {
            self.sum_for_seed += bar.close;
            if self.seen == self.period {
                self.value = Some(self.sum_for_seed / self.period as f64);
            }
            return;
        }
        let prev = self.value.expect("EMA value seeded after warm-up");
        self.value = Some(prev + self.alpha * (bar.close - prev));
    }

    fn value(&self) -> Option<f64> {
        self.value
    }
}
