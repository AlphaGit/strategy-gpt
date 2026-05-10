//! Engine-side sanity bounds for backtest validity.
//!
//! These are NOT live-trading risk controls. They catch degenerate hypotheses
//! that would distort backtest metrics — e.g., a strategy that sizes 1000×
//! equity on every bar.

use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
pub struct SanityBounds {
    /// Maximum absolute size of any single submitted intent, in instrument
    /// units (e.g., shares, contracts).
    pub max_intent_size: f64,
    /// Maximum total absolute position size per symbol.
    pub max_position_size: f64,
}

impl Default for SanityBounds {
    fn default() -> Self {
        Self {
            max_intent_size: 1.0e9,
            max_position_size: 1.0e9,
        }
    }
}

impl SanityBounds {
    pub fn check_intent_size(&self, size: f64) -> Result<(), String> {
        if !size.is_finite() || size <= 0.0 {
            return Err(format!("intent size must be finite and positive: {size}"));
        }
        if size > self.max_intent_size {
            return Err(format!(
                "intent size {size} exceeds sanity bound {}",
                self.max_intent_size
            ));
        }
        Ok(())
    }

    pub fn check_position_size(&self, size: f64) -> Result<(), String> {
        if size.abs() > self.max_position_size {
            return Err(format!(
                "position size {size} exceeds sanity bound {}",
                self.max_position_size
            ));
        }
        Ok(())
    }
}
