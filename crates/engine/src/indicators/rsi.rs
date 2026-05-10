use engine_rt::Bar;

use super::Indicator;

/// Wilder's RSI over close prices.
#[derive(Clone, Debug)]
pub struct Rsi {
    name: String,
    period: usize,
    seen: usize,
    prev_close: Option<f64>,
    avg_gain: f64,
    avg_loss: f64,
    value: Option<f64>,
}

impl Rsi {
    pub fn new(name: impl Into<String>, period: usize) -> Self {
        assert!(period > 0, "RSI period must be positive");
        Self {
            name: name.into(),
            period,
            seen: 0,
            prev_close: None,
            avg_gain: 0.0,
            avg_loss: 0.0,
            value: None,
        }
    }
}

impl Indicator for Rsi {
    fn name(&self) -> &str {
        &self.name
    }

    fn update(&mut self, bar: &Bar) {
        let close = bar.close;
        let Some(prev) = self.prev_close else {
            self.prev_close = Some(close);
            return;
        };
        let change = close - prev;
        let gain = change.max(0.0);
        let loss = (-change).max(0.0);

        self.seen += 1;
        if self.seen <= self.period {
            // Accumulate the seed averages.
            self.avg_gain += gain;
            self.avg_loss += loss;
            if self.seen == self.period {
                self.avg_gain /= self.period as f64;
                self.avg_loss /= self.period as f64;
                self.value = Some(compute_rsi(self.avg_gain, self.avg_loss));
            }
        } else {
            let p = self.period as f64;
            self.avg_gain = (self.avg_gain * (p - 1.0) + gain) / p;
            self.avg_loss = (self.avg_loss * (p - 1.0) + loss) / p;
            self.value = Some(compute_rsi(self.avg_gain, self.avg_loss));
        }
        self.prev_close = Some(close);
    }

    fn value(&self) -> Option<f64> {
        self.value
    }
}

fn compute_rsi(avg_gain: f64, avg_loss: f64) -> f64 {
    if avg_loss == 0.0 {
        return 100.0;
    }
    let rs = avg_gain / avg_loss;
    100.0 - (100.0 / (1.0 + rs))
}
