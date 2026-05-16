//! In-process synchronous backtest executor.
//!
//! `run_one` drives a single strategy across a stream of bars and returns a
//! [`BacktestResult`]. The `engine-worker` binary and the coordinator wrap
//! this same loop with IPC and resource limits; the loop itself is shared.

use std::collections::HashMap;

use chrono::{DateTime, Utc};
use engine_rt::{Bar, Strategy, RUNNER_VERSION};
use rand_chacha::rand_core::SeedableRng;
use rand_chacha::ChaCha8Rng;

use crate::equity_recorder::EquityRecorder;
use crate::fill_model::FillModel;
use crate::indicators::IndicatorRegistry;
use crate::intent::IntentBook;
use crate::metrics::compute_metrics;
use crate::position_book::PositionBook;
use crate::result::{BacktestResult, ResultMeta};
use crate::runtime::RuntimeContext;
use crate::spec::{BatchSpec, EngineConfig, RunSpec};
use crate::trade_log::TradeLog;

/// Annualization factor for daily bars.
const DAILY_ANNUALIZATION: f64 = 252.0;

#[derive(Clone, Debug, thiserror::Error)]
pub enum ExecutionError {
    #[error("strategy panicked or returned an error: {0}")]
    StrategyError(String),
    #[error("dataset is empty")]
    EmptyDataset,
}

/// Run a single strategy against a stream of bars.
///
/// The caller owns the strategy instance (typically constructed from a freshly
/// loaded artifact). `indicators` is a freshly constructed registry — the
/// executor does not share state across runs.
pub fn run_one(
    strategy: &mut dyn Strategy,
    bars: &[Bar],
    run: &RunSpec,
    engine: &EngineConfig,
    mut indicators: IndicatorRegistry,
    strategy_artifact: &str,
    dataset_manifest: &str,
) -> Result<BacktestResult, ExecutionError> {
    if bars.is_empty() {
        return Err(ExecutionError::EmptyDataset);
    }

    let mut intents = IntentBook::new();
    let mut positions = PositionBook::new();
    let mut signals = Vec::new();
    let mut decisions = Vec::new();
    let mut state: HashMap<String, serde_json::Value> = HashMap::new();
    // Surface `RunSpec.params` to the strategy under a reserved key. Strategies
    // read this via `Context::state_get(&StateKey::from("__params__"))` and
    // deserialize the JSON into their own typed parameter struct. Using the
    // existing state surface (rather than adding a new method to `Context`)
    // keeps the ABI shape stable across runner versions.
    state.insert("__params__".to_string(), run.params.clone());
    let mut trades = TradeLog::new();
    let mut equity = EquityRecorder::new(engine.initial_capital);
    let mut realized_pnl_running = 0.0;
    let mut last_marks: HashMap<String, (DateTime<Utc>, f64)> = HashMap::new();
    // Symbol set held in any bar in the slice (for equity exposure calc).
    let mut symbol_set: Vec<String> = Vec::new();

    // Seeded RNG threaded through to fee perturbation; reserved for
    // stochastic stress modes.
    let _rng = ChaCha8Rng::seed_from_u64(run.seed);

    {
        let mut ctx = RuntimeContext {
            now: bars[0].ts,
            intents: &mut intents,
            positions: &mut positions,
            indicators: &indicators,
            signals: &mut signals,
            decisions: &mut decisions,
            state: &mut state,
            sanity: engine.sanity,
        };
        strategy
            .on_init(&mut ctx)
            .map_err(|e| ExecutionError::StrategyError(format!("on_init: {e}")))?;
    }

    let fee_fn = |_order: &engine_rt::Order, price: f64| {
        // Fixed commission + slippage in price terms applied to fill notional.
        // Real slippage (price-impacting) lands with the Slippage stress mode.
        let slippage = price.abs() * engine.slippage_bps;
        engine.commission_per_fill + slippage
    };

    for bar in bars.iter().filter(|b| run.slice.contains(b.ts)) {
        if !symbol_set.iter().any(|s| s == &bar.symbol) {
            symbol_set.push(bar.symbol.clone());
        }
        last_marks.insert(bar.symbol.clone(), (bar.ts, bar.close));

        // Update indicators on every bar regardless of strategy ordering.
        indicators.update_all(bar);

        // Phase 1: NextBarOpen fills for intents submitted on the previous bar.
        let pre_bar_fills = if engine.fill_model == FillModel::NextBarOpen {
            intents.try_fill(bar, FillModel::NextBarOpen, fee_fn)
        } else {
            Vec::new()
        };
        for fill in &pre_bar_fills {
            positions.apply_fill(fill);
            realized_pnl_running = positions_realized_total(&positions, &symbol_set);
            let active_signals: Vec<String> = signals
                .iter()
                .filter(|s| s.fired)
                .rev()
                .take(8)
                .map(|s| s.name.clone())
                .collect();
            // Reason taken from the originating order via the intent book; we
            // do not track that here, so leave None.
            trades.record_fill(fill, None, &active_signals);

            let mut ctx = RuntimeContext {
                now: bar.ts,
                intents: &mut intents,
                positions: &mut positions,
                indicators: &indicators,
                signals: &mut signals,
                decisions: &mut decisions,
                state: &mut state,
                sanity: engine.sanity,
            };
            strategy
                .on_fill(fill, &mut ctx)
                .map_err(|e| ExecutionError::StrategyError(format!("on_fill: {e}")))?;
        }

        // Phase 2: strategy gets the bar.
        {
            let mut ctx = RuntimeContext {
                now: bar.ts,
                intents: &mut intents,
                positions: &mut positions,
                indicators: &indicators,
                signals: &mut signals,
                decisions: &mut decisions,
                state: &mut state,
                sanity: engine.sanity,
            };
            strategy
                .on_bar(bar, &mut ctx)
                .map_err(|e| ExecutionError::StrategyError(format!("on_bar: {e}")))?;
        }

        // Phase 3: CurrentBarClose fills for intents submitted in this bar.
        let post_bar_fills = if engine.fill_model == FillModel::CurrentBarClose {
            intents.try_fill(bar, FillModel::CurrentBarClose, fee_fn)
        } else {
            Vec::new()
        };
        for fill in &post_bar_fills {
            positions.apply_fill(fill);
            realized_pnl_running = positions_realized_total(&positions, &symbol_set);
            let active_signals: Vec<String> = signals
                .iter()
                .filter(|s| s.fired)
                .rev()
                .take(8)
                .map(|s| s.name.clone())
                .collect();
            trades.record_fill(fill, None, &active_signals);

            let mut ctx = RuntimeContext {
                now: bar.ts,
                intents: &mut intents,
                positions: &mut positions,
                indicators: &indicators,
                signals: &mut signals,
                decisions: &mut decisions,
                state: &mut state,
                sanity: engine.sanity,
            };
            strategy
                .on_fill(fill, &mut ctx)
                .map_err(|e| ExecutionError::StrategyError(format!("on_fill: {e}")))?;
        }

        // Phase 4: equity snapshot for this bar.
        equity.record(
            bar.ts,
            &positions,
            realized_pnl_running,
            |sym| last_marks.get(sym).map(|(_, p)| *p).unwrap_or(0.0),
            &symbol_set,
        );
    }

    // End of run: close any open positions against last mark, expire pending.
    intents.expire_all_pending();
    trades.close_remaining(&last_marks);

    {
        let mut ctx = RuntimeContext {
            now: bars.last().expect("non-empty checked").ts,
            intents: &mut intents,
            positions: &mut positions,
            indicators: &indicators,
            signals: &mut signals,
            decisions: &mut decisions,
            state: &mut state,
            sanity: engine.sanity,
        };
        strategy
            .on_end(&mut ctx)
            .map_err(|e| ExecutionError::StrategyError(format!("on_end: {e}")))?;
    }

    let trade_records = trades.into_closed();
    let equity_points = equity.into_points();
    let metrics = compute_metrics(&equity_points, &trade_records, DAILY_ANNUALIZATION);

    Ok(BacktestResult {
        meta: ResultMeta {
            strategy_artifact: strategy_artifact.into(),
            dataset_manifest: dataset_manifest.into(),
            seed: run.seed,
            runner_version: RUNNER_VERSION,
        },
        metrics,
        trades: trade_records,
        signals,
        equity: equity_points,
        exec_log: decisions,
        regimes: Vec::new(),
        stress: None,
        sensitivity: None,
    })
}

fn positions_realized_total(positions: &PositionBook, symbols: &[String]) -> f64 {
    symbols.iter().map(|s| positions.realized_pnl(s)).sum()
}

/// Strategy factory used by the batch runner: produces a fresh strategy
/// instance per run so state does not leak across configurations.
pub trait StrategyFactory {
    fn make(&self) -> Box<dyn Strategy>;
}

impl<F> StrategyFactory for F
where
    F: Fn() -> Box<dyn Strategy>,
{
    fn make(&self) -> Box<dyn Strategy> {
        (self)()
    }
}

#[derive(Clone, Debug, thiserror::Error)]
pub enum BatchError {
    #[error("run {index} failed: {source}")]
    Run {
        index: usize,
        source: ExecutionError,
    },
}

/// Run every [`RunSpec`] in `batch.runs` in order against the same bar stream.
/// Aborts the whole batch on the first failure (per spec
/// `backtest-engine::Abort-on-failure`); returns a structured error pointing
/// at the failing index.
pub fn run_batch(
    batch: &BatchSpec,
    bars: &[Bar],
    factory: &dyn StrategyFactory,
    indicators_factory: impl Fn() -> IndicatorRegistry,
    dataset_manifest: &str,
) -> Result<Vec<BacktestResult>, BatchError> {
    let mut out = Vec::with_capacity(batch.runs.len());
    for (i, run) in batch.runs.iter().enumerate() {
        let mut strategy = factory.make();
        let indicators = indicators_factory();
        let result = run_one(
            strategy.as_mut(),
            bars,
            run,
            &batch.engine,
            indicators,
            &batch.strategy.0,
            dataset_manifest,
        )
        .map_err(|source| BatchError::Run { index: i, source })?;
        out.push(result);
    }
    Ok(out)
}
