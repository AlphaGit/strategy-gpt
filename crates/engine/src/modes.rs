//! Implementations of stress and sensitivity [`Mode`]s.
//!
//! Each function takes the baseline configuration plus a strategy factory and
//! returns the corresponding sub-results, leaving the original [`run_one`]
//! call untouched. Caller composes: do the plain run first, then call
//! `apply_modes` to attach sub-results.

use std::collections::HashSet;

use engine_rt::Bar;
use rand::seq::SliceRandom;
use rand_chacha::rand_core::SeedableRng;
use rand_chacha::ChaCha8Rng;

use crate::executor::{run_one, ExecutionError, StrategyFactory};
use crate::indicators::IndicatorRegistry;
use crate::result::{
    BacktestMetrics, BacktestResult, SensitivityPoint, SensitivityResult, StressResult,
    StressScenario,
};
use crate::spec::{EngineConfig, Mode, RunSpec, TimeRange};

/// Apply every non-`Plain` mode declared by `run.modes` to `baseline`, mutating
/// `baseline.stress` / `baseline.sensitivity` in place. Errors abort the whole
/// `apply_modes` call (consistent with batch abort-on-failure semantics).
#[allow(clippy::too_many_arguments)]
pub fn apply_modes(
    baseline: &mut BacktestResult,
    factory: &dyn StrategyFactory,
    indicators_factory: &dyn Fn() -> IndicatorRegistry,
    bars: &[Bar],
    run: &RunSpec,
    engine: &EngineConfig,
    strategy_artifact: &str,
    dataset_manifest: &str,
) -> Result<(), ExecutionError> {
    for mode in &run.modes {
        match mode {
            Mode::Plain => {}
            Mode::MonteCarlo { n, block_size } => {
                let scenarios = monte_carlo(
                    *n,
                    *block_size,
                    factory,
                    indicators_factory,
                    bars,
                    run,
                    engine,
                    strategy_artifact,
                    dataset_manifest,
                )?;
                attach_stress(baseline, scenarios);
            }
            Mode::Slippage { bps_grid } => {
                let scenarios = slippage_sweep(
                    bps_grid,
                    factory,
                    indicators_factory,
                    bars,
                    run,
                    engine,
                    strategy_artifact,
                    dataset_manifest,
                )?;
                attach_stress(baseline, scenarios);
            }
            Mode::RegimeFilter { ranges } => {
                let scenarios = regime_filter(
                    ranges,
                    factory,
                    indicators_factory,
                    bars,
                    run,
                    engine,
                    strategy_artifact,
                    dataset_manifest,
                )?;
                attach_stress(baseline, scenarios);
            }
            Mode::Sensitivity { param, values } => {
                let result = sensitivity_sweep(
                    param,
                    values,
                    factory,
                    indicators_factory,
                    bars,
                    run,
                    engine,
                    strategy_artifact,
                    dataset_manifest,
                )?;
                baseline.sensitivity = Some(result);
            }
        }
    }
    Ok(())
}

fn attach_stress(baseline: &mut BacktestResult, scenarios: Vec<StressScenario>) {
    let entry = baseline
        .stress
        .get_or_insert(StressResult { scenarios: vec![] });
    entry.scenarios.extend(scenarios);
}

#[allow(clippy::too_many_arguments)]
fn monte_carlo(
    n: u32,
    block_size: u32,
    factory: &dyn StrategyFactory,
    indicators_factory: &dyn Fn() -> IndicatorRegistry,
    bars: &[Bar],
    run: &RunSpec,
    engine: &EngineConfig,
    artifact: &str,
    manifest: &str,
) -> Result<Vec<StressScenario>, ExecutionError> {
    if n == 0 || block_size == 0 || bars.len() < block_size as usize {
        return Ok(vec![]);
    }
    // Mode-specific salt: deterministic per (seed, mode_kind) so MC samples
    // do not collide with the baseline run's RNG stream.
    let mut rng = ChaCha8Rng::seed_from_u64(run.seed.wrapping_add(0xC0DE_FEED));
    let mut metrics_acc: Vec<BacktestMetrics> = Vec::with_capacity(n as usize);
    let blocks: Vec<&[Bar]> = bars.chunks(block_size as usize).collect();
    for _ in 0..n {
        let mut resampled: Vec<Bar> = Vec::with_capacity(bars.len());
        while resampled.len() < bars.len() {
            let block = blocks
                .choose(&mut rng)
                .copied()
                .expect("blocks non-empty when n>0 and bars>=block_size");
            for b in block {
                if resampled.len() >= bars.len() {
                    break;
                }
                resampled.push(b.clone());
            }
        }
        // Re-stamp timestamps to preserve chronological order even though
        // values were resampled, so the executor's timestamp ordering holds.
        for (i, b) in resampled.iter_mut().enumerate() {
            b.ts = bars[i].ts;
        }
        let mut strategy = factory.make();
        let result = run_one(
            strategy.as_mut(),
            &resampled,
            run,
            engine,
            indicators_factory(),
            artifact,
            manifest,
        )?;
        metrics_acc.push(result.metrics);
    }
    let aggregated = aggregate_metrics(&metrics_acc);
    Ok(vec![StressScenario {
        name: format!("monte_carlo:n={n},block={block_size}"),
        perturbation: serde_json::json!({
            "n": n,
            "block_size": block_size,
            "iterations": metrics_acc.len(),
        }),
        metrics: aggregated,
    }])
}

#[allow(clippy::too_many_arguments)]
fn slippage_sweep(
    bps_grid: &[f64],
    factory: &dyn StrategyFactory,
    indicators_factory: &dyn Fn() -> IndicatorRegistry,
    bars: &[Bar],
    run: &RunSpec,
    engine: &EngineConfig,
    artifact: &str,
    manifest: &str,
) -> Result<Vec<StressScenario>, ExecutionError> {
    let mut out = Vec::with_capacity(bps_grid.len());
    for &bps in bps_grid {
        let mut cfg = engine.clone();
        cfg.slippage_bps = bps;
        let mut strategy = factory.make();
        let result = run_one(
            strategy.as_mut(),
            bars,
            run,
            &cfg,
            indicators_factory(),
            artifact,
            manifest,
        )?;
        out.push(StressScenario {
            name: format!("slippage:{bps}"),
            perturbation: serde_json::json!({ "slippage_bps": bps }),
            metrics: result.metrics,
        });
    }
    Ok(out)
}

#[allow(clippy::too_many_arguments)]
fn regime_filter(
    ranges: &[TimeRange],
    factory: &dyn StrategyFactory,
    indicators_factory: &dyn Fn() -> IndicatorRegistry,
    bars: &[Bar],
    run: &RunSpec,
    engine: &EngineConfig,
    artifact: &str,
    manifest: &str,
) -> Result<Vec<StressScenario>, ExecutionError> {
    let mut out = Vec::with_capacity(ranges.len());
    for (i, r) in ranges.iter().enumerate() {
        let filtered: Vec<Bar> = bars.iter().filter(|b| r.contains(b.ts)).cloned().collect();
        if filtered.is_empty() {
            out.push(StressScenario {
                name: format!("regime_filter:{i}:empty"),
                perturbation: serde_json::json!({
                    "start": r.start, "end": r.end, "bars": 0,
                }),
                metrics: BacktestMetrics::empty(),
            });
            continue;
        }
        let mut strategy = factory.make();
        let result = run_one(
            strategy.as_mut(),
            &filtered,
            run,
            engine,
            indicators_factory(),
            artifact,
            manifest,
        )?;
        out.push(StressScenario {
            name: format!("regime_filter:{i}"),
            perturbation: serde_json::json!({
                "start": r.start, "end": r.end, "bars": filtered.len(),
            }),
            metrics: result.metrics,
        });
    }
    Ok(out)
}

#[allow(clippy::too_many_arguments)]
fn sensitivity_sweep(
    param: &str,
    values: &[f64],
    factory: &dyn StrategyFactory,
    indicators_factory: &dyn Fn() -> IndicatorRegistry,
    bars: &[Bar],
    run: &RunSpec,
    engine: &EngineConfig,
    artifact: &str,
    manifest: &str,
) -> Result<SensitivityResult, ExecutionError> {
    let mut points = Vec::with_capacity(values.len());
    let mut seen: HashSet<u64> = HashSet::new();
    for &v in values {
        // Dedup identical f64 values; cheap.
        let bits = v.to_bits();
        if !seen.insert(bits) {
            continue;
        }
        let mut overridden = run.clone();
        set_param(&mut overridden.params, param, v);
        let mut strategy = factory.make();
        let result = run_one(
            strategy.as_mut(),
            bars,
            &overridden,
            engine,
            indicators_factory(),
            artifact,
            manifest,
        )?;
        points.push(SensitivityPoint {
            value: v,
            metrics: result.metrics,
        });
    }
    Ok(SensitivityResult {
        param: param.into(),
        points,
    })
}

/// Set or replace a numeric param in a JSON value. If `params` is not an
/// object, replace it with a fresh object containing the single key. Strategies
/// that consume non-object params will simply not read the new shape and the
/// sensitivity sweep will land identical metrics across values; that is the
/// caller's contract problem, not the engine's.
fn set_param(params: &mut serde_json::Value, name: &str, value: f64) {
    if !params.is_object() {
        *params = serde_json::Value::Object(Default::default());
    }
    if let serde_json::Value::Object(map) = params {
        map.insert(name.into(), serde_json::Value::from(value));
    }
}

fn aggregate_metrics(samples: &[BacktestMetrics]) -> BacktestMetrics {
    if samples.is_empty() {
        return BacktestMetrics::empty();
    }
    let n = samples.len() as f64;
    let mean = |f: fn(&BacktestMetrics) -> f64| -> f64 { samples.iter().map(f).sum::<f64>() / n };
    BacktestMetrics {
        sharpe: mean(|m| m.sharpe),
        sortino: mean(|m| m.sortino),
        profit_factor: mean(|m| {
            if m.profit_factor.is_finite() {
                m.profit_factor
            } else {
                0.0
            }
        }),
        win_ratio: mean(|m| m.win_ratio),
        max_drawdown: mean(|m| m.max_drawdown),
        annualized_return: mean(|m| m.annualized_return),
        n_trades: (samples.iter().map(|m| m.n_trades as f64).sum::<f64>() / n) as u32,
        avg_trade_length_bars: mean(|m| m.avg_trade_length_bars),
    }
}
