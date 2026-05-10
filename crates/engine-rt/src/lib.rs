//! Strategy runtime — public surface for LLM-emitted strategies.
//!
//! Strategies implement the [`Strategy`] trait and interact with the engine
//! exclusively through the [`Context`] capability handle. The trait is sealed:
//! third-party crates cannot implement it without going through this crate's
//! `Strategy` blanket — every strategy must be authored against this surface.

mod bar;
mod context;
mod decision;
mod error;
mod indicator;
mod order;
mod sealed;
mod signal;
mod state;
mod strategy;
mod version;

pub use bar::{Bar, Resolution};
pub use context::Context;
pub use decision::DecisionEvent;
pub use error::{Error, Result};
pub use indicator::{IndicatorHandle, IndicatorName};
pub use order::{Fill, Order, OrderId, Position, Side};
pub use signal::{SignalEvent, SignalName};
pub use state::StateKey;
pub use strategy::{Strategy, StrategyMeta};
pub use version::{RunnerVersion, RUNNER_VERSION};

/// Re-export of the seal so the macro generated for new strategies can refer to it.
#[doc(hidden)]
pub use sealed::Sealed;
