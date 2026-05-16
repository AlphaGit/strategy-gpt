//! Backtest engine — internal implementations of the [`engine_rt::Context`]
//! trait, the fill simulator, position accounting, the indicator registry,
//! and the in-process synchronous run loop.
//!
//! See specs `backtest-engine` and `strategy-runtime`.

pub mod coordinator;
pub mod equity_recorder;
pub mod executor;
pub mod fill_model;
pub mod indicators;
pub mod intent;
pub mod logging;
pub mod metrics;
pub mod modes;
pub mod plugin;
pub mod position_book;
pub mod regime;
pub mod result;
pub mod runtime;
pub mod sanity;
pub mod spec;
pub mod trade_log;
pub mod wire;

pub use coordinator::{Coordinator, CoordinatorError, ResourceCaps};
pub use equity_recorder::EquityRecorder;
pub use executor::{run_batch, run_one, BatchError, ExecutionError, StrategyFactory};
pub use fill_model::FillModel;
pub use indicators::{Indicator, IndicatorRegistry};
pub use intent::{IntentBook, IntentStatus, PendingIntent};
pub use metrics::compute_metrics;
pub use modes::apply_modes;
pub use plugin::{OwnedPluginStrategy, PluginError, PluginFactory, PluginStrategy, StrategyPlugin};
pub use position_book::PositionBook;
pub use regime::annotate_regimes;
pub use result::{
    BacktestMetrics, BacktestResult, EquityPoint, RegimeTag, ResultMeta, RunResult,
    SensitivityPoint, SensitivityResult, StressResult, StressScenario, Trade,
};
pub use runtime::RuntimeContext;
pub use sanity::SanityBounds;
pub use spec::{
    BatchSpec, DatasetRef, EngineConfig, FailureMode, Mode, ParamSet, RunSpec, StrategyArtifactRef,
    TimeRange,
};
pub use trade_log::TradeLog;
