use std::collections::VecDeque;

use engine_rt::Bar;

use super::Indicator;

/// Annualized realized volatility computed as the standard deviation of log
/// returns over a rolling window. Annualization factor assumes daily bars
/// (sqrt(252)); strategies operating on other resolutions can register their
/// own variant.
#[derive(Clone, Debug)]
pub struct RealizedVol {
    name: String,
    window: usize,
    annualization: f64,
    closes: VecDeque<f64>,
    returns: VecDeque<f64>,
    sum: f64,
    sum_sq: f64,
}

impl RealizedVol {
    pub fn new(name: impl Into<String>, window: usize) -> Self {
        assert!(window > 1, "realized vol window must be greater than 1");
        Self {
            name: name.into(),
            window,
            annualization: (252.0_f64).sqrt(),
            closes: VecDeque::with_capacity(2),
            returns: VecDeque::with_capacity(window),
            sum: 0.0,
            sum_sq: 0.0,
        }
    }

    pub fn with_annualization(mut self, factor: f64) -> Self {
        self.annualization = factor;
        self
    }
}

impl Indicator for RealizedVol {
    fn name(&self) -> &str {
        &self.name
    }

    fn update(&mut self, bar: &Bar) {
        let close = bar.close;
        let prev_close = self.closes.back().copied();
        self.closes.push_back(close);
        if self.closes.len() > 2 {
            self.closes.pop_front();
        }
        let Some(prev) = prev_close else {
            return;
        };
        if prev <= 0.0 || close <= 0.0 {
            return;
        }
        let ret = (close / prev).ln();
        self.returns.push_back(ret);
        self.sum += ret;
        self.sum_sq += ret * ret;
        if self.returns.len() > self.window {
            if let Some(old) = self.returns.pop_front() {
                self.sum -= old;
                self.sum_sq -= old * old;
            }
        }
    }

    fn value(&self) -> Option<f64> {
        if self.returns.len() < self.window {
            return None;
        }
        let n = self.returns.len() as f64;
        let mean = self.sum / n;
        let variance = (self.sum_sq / n) - mean * mean;
        if variance < 0.0 {
            return Some(0.0);
        }
        Some(variance.sqrt() * self.annualization)
    }
}
