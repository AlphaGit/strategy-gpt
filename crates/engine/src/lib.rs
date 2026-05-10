//! Backtest engine — internal implementations of the [`engine_rt::Context`]
//! trait, the fill simulator, position accounting, and the indicator registry.
//!
//! See specs `backtest-engine` and `strategy-runtime`.

pub mod fill_model;
pub mod indicators;
pub mod intent;
pub mod position_book;
pub mod runtime;
pub mod sanity;

pub use fill_model::FillModel;
pub use indicators::{Indicator, IndicatorRegistry};
pub use intent::{IntentBook, IntentStatus, PendingIntent};
pub use position_book::PositionBook;
pub use runtime::RuntimeContext;
pub use sanity::SanityBounds;
