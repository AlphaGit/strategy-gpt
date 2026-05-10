use thiserror::Error;

/// Errors surfaced through the [`crate::Context`] API during a backtest.
///
/// Every variant describes a backtest-time condition. None of these are live
/// trading errors — strategy-gpt is a research harness, not a runtime trading
/// system.
#[derive(Debug, Error)]
pub enum Error {
    /// The submitted trade intent is malformed (e.g., zero size, NaN price).
    #[error("invalid order: {0}")]
    InvalidOrder(String),

    /// An `on_fill` callback referenced an order id the engine does not know.
    /// Internal sanity check; should not occur during normal backtest flow.
    #[error("unknown order id: {0}")]
    UnknownOrder(u64),

    #[error("indicator not found: {0}")]
    UnknownIndicator(String),

    #[error("state key not found: {0}")]
    UnknownStateKey(String),

    /// Engine sanity bound for backtest validity (e.g., a strategy attempted to
    /// size 1000× equity). This is *not* a live-trading risk control; it
    /// catches degenerate hypotheses whose results would otherwise distort
    /// metrics.
    #[error("risk cap violated: {0}")]
    RiskCap(String),

    #[error("strategy aborted: {0}")]
    Abort(String),
}

pub type Result<T> = std::result::Result<T, Error>;
