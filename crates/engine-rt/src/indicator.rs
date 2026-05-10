use serde::{Deserialize, Serialize};

pub type IndicatorName = String;

/// Opaque handle to an engine-provided indicator. The engine owns the indicator
/// state and computes values bar-by-bar; strategies read the latest value via
/// [`crate::Context::read_indicator`].
#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Serialize, Deserialize)]
pub struct IndicatorHandle(pub u32);
