//! Canonical metric name registry sourced from [`engine::BacktestMetrics`].

use engine::BacktestMetrics;

/// The set of metric names the engine emits. Objective specs may reference
/// only these.
pub const ENGINE_METRICS: &[&str] = &[
    "sharpe",
    "sortino",
    "profit_factor",
    "win_ratio",
    "max_drawdown",
    "annualized_return",
    "n_trades",
    "avg_trade_length_bars",
];

pub fn is_valid_metric(name: &str) -> bool {
    ENGINE_METRICS.iter().any(|m| *m == name)
}

/// Read a numeric metric value by name from a [`BacktestMetrics`] snapshot.
/// Returns `None` if the name is not in [`ENGINE_METRICS`].
pub fn metric_value(metrics: &BacktestMetrics, name: &str) -> Option<f64> {
    match name {
        "sharpe" => Some(metrics.sharpe),
        "sortino" => Some(metrics.sortino),
        "profit_factor" => Some(metrics.profit_factor),
        "win_ratio" => Some(metrics.win_ratio),
        "max_drawdown" => Some(metrics.max_drawdown),
        "annualized_return" => Some(metrics.annualized_return),
        "n_trades" => Some(metrics.n_trades as f64),
        "avg_trade_length_bars" => Some(metrics.avg_trade_length_bars),
        _ => None,
    }
}
