use engine_rt::Bar;

use super::Indicator;

/// Wilder's Average True Range over `period` bars.
#[derive(Clone, Debug)]
pub struct Atr {
    name: String,
    period: usize,
    seen: usize,
    prev_close: Option<f64>,
    sum_for_seed: f64,
    value: Option<f64>,
}

impl Atr {
    pub fn new(name: impl Into<String>, period: usize) -> Self {
        assert!(period > 0, "ATR period must be positive");
        Self {
            name: name.into(),
            period,
            seen: 0,
            prev_close: None,
            sum_for_seed: 0.0,
            value: None,
        }
    }
}

impl Indicator for Atr {
    fn name(&self) -> &str {
        &self.name
    }

    fn update(&mut self, bar: &Bar) {
        let tr = match self.prev_close {
            None => bar.high - bar.low,
            Some(pc) => {
                let a = bar.high - bar.low;
                let b = (bar.high - pc).abs();
                let c = (bar.low - pc).abs();
                a.max(b).max(c)
            }
        };
        self.seen += 1;
        if self.seen <= self.period {
            self.sum_for_seed += tr;
            if self.seen == self.period {
                self.value = Some(self.sum_for_seed / self.period as f64);
            }
        } else {
            let p = self.period as f64;
            let prev = self.value.expect("ATR seeded after warm-up");
            self.value = Some((prev * (p - 1.0) + tr) / p);
        }
        self.prev_close = Some(bar.close);
    }

    fn value(&self) -> Option<f64> {
        self.value
    }
}
