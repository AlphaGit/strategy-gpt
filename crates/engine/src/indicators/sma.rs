use std::collections::VecDeque;

use engine_rt::Bar;

use super::Indicator;

#[derive(Clone, Debug)]
pub struct Sma {
    name: String,
    window: usize,
    buffer: VecDeque<f64>,
    sum: f64,
}

impl Sma {
    pub fn new(name: impl Into<String>, window: usize) -> Self {
        assert!(window > 0, "SMA window must be positive");
        Self {
            name: name.into(),
            window,
            buffer: VecDeque::with_capacity(window),
            sum: 0.0,
        }
    }
}

impl Indicator for Sma {
    fn name(&self) -> &str {
        &self.name
    }

    fn update(&mut self, bar: &Bar) {
        self.buffer.push_back(bar.close);
        self.sum += bar.close;
        if self.buffer.len() > self.window {
            if let Some(old) = self.buffer.pop_front() {
                self.sum -= old;
            }
        }
    }

    fn value(&self) -> Option<f64> {
        if self.buffer.len() < self.window {
            None
        } else {
            Some(self.sum / self.window as f64)
        }
    }
}
