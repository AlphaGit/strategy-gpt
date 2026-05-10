use crate::error::Result;
use crate::indicator::IndicatorName;
use crate::order::{OrderId, Position, Side};
use crate::state::StateKey;
use serde_json::Value;

/// Capability handle passed to every strategy lifecycle method.
///
/// `Context` is the strategy's only doorway into engine-managed state during a
/// backtest. The runtime is a research platform — there is no live order book,
/// no broker, no asynchronous order management. A strategy expresses *trade
/// intents* via [`Context::submit_order`]; the engine fills them according to
/// its configured fill model and reports back through [`crate::Strategy::on_fill`].
///
/// Strategies always receive `&mut dyn Context`; they never construct one
/// themselves.
pub trait Context {
    /// Submit a trade intent. The engine assigns an [`OrderId`] and simulates
    /// the fill at its configured time (typically next bar open or current bar
    /// close). There is no cancellation: if a strategy changes its mind, it
    /// submits a closing intent on the next bar.
    fn submit_order(
        &mut self,
        symbol: &str,
        side: Side,
        size: f64,
        limit_price: Option<f64>,
        stop_price: Option<f64>,
        reason: Option<&str>,
    ) -> Result<OrderId>;

    /// Current accounting position used for backtest decisions. Realized and
    /// unrealized P&L are computed by the engine post-hoc as part of metrics —
    /// they are not exposed here because strategies do not need them to decide.
    fn get_position(&self, symbol: &str) -> Position;

    fn log_signal(
        &mut self,
        name: &str,
        value: f64,
        fired: bool,
        suppressed_by: Option<&str>,
    );

    fn log_decision(&mut self, event: &str, details: Value);

    fn read_indicator(&self, name: &IndicatorName) -> Result<f64>;

    fn state_get(&self, key: &StateKey) -> Result<Option<Value>>;

    fn state_set(&mut self, key: StateKey, value: Value) -> Result<()>;
}
