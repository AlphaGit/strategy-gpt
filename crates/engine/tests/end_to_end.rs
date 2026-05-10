//! End-to-end executor + batch tests.
//!
//! Exercises the public `engine` API with a synthetic test strategy. The
//! `Strategy` trait is sealed via `engine_rt::Sealed`; the seal is
//! `#[doc(hidden)] pub` precisely so the engine crate (and its tests) can
//! implement it for purpose-built test types.

use chrono::{DateTime, TimeZone, Utc};
use engine::executor::StrategyFactory;
use engine::indicators::IndicatorRegistry;
use engine::result::BacktestResult;
use engine::spec::{BatchSpec, DatasetRef, EngineConfig, RunSpec, StrategyArtifactRef, TimeRange};
use engine::{run_batch, run_one, FillModel};
use engine_rt::{
    Bar, Context, Fill, Resolution, Result as RtResult, Sealed, Side, Strategy, StrategyMeta,
};

fn ts(d: u32) -> DateTime<Utc> {
    Utc.with_ymd_and_hms(2024, 1, d, 0, 0, 0).unwrap()
}

fn bars(closes: &[f64]) -> Vec<Bar> {
    closes
        .iter()
        .enumerate()
        .map(|(i, c)| Bar {
            symbol: "VXX".into(),
            ts: ts(1 + i as u32),
            resolution: Resolution::Day,
            open: c - 0.5,
            high: c + 1.0,
            low: c - 1.0,
            close: *c,
            volume: 1_000.0,
        })
        .collect()
}

/// Buys on the first bar, sells on bar `exit_after` bars later.
struct BuyAndHoldOnce {
    bought: bool,
    bars_held: u32,
    exit_after: u32,
    on_fill_calls: u32,
}

impl Sealed for BuyAndHoldOnce {}

impl Strategy for BuyAndHoldOnce {
    fn metadata(&self) -> StrategyMeta {
        StrategyMeta::new("buy_and_hold_once", "0.1.0", "test", "test fixture")
    }

    fn on_bar(&mut self, _bar: &Bar, ctx: &mut dyn Context) -> RtResult<()> {
        if !self.bought {
            ctx.submit_order("VXX", Side::Long, 100.0, None, None, Some("entry"))?;
            self.bought = true;
        } else {
            self.bars_held += 1;
            if self.bars_held == self.exit_after {
                ctx.submit_order("VXX", Side::Short, 100.0, None, None, Some("exit"))?;
            }
        }
        Ok(())
    }

    fn on_fill(&mut self, _fill: &Fill, _ctx: &mut dyn Context) -> RtResult<()> {
        self.on_fill_calls += 1;
        Ok(())
    }
}

fn fixture_run(slice: TimeRange, seed: u64) -> RunSpec {
    RunSpec {
        params: serde_json::Value::Null,
        modes: vec![engine::Mode::Plain],
        seed,
        slice,
    }
}

fn fixture_batch(runs: Vec<RunSpec>) -> BatchSpec {
    BatchSpec {
        strategy: StrategyArtifactRef("test-artifact".into()),
        dataset: DatasetRef("test-dataset".into()),
        runs,
        engine: EngineConfig {
            fill_model: FillModel::NextBarOpen,
            initial_capital: 100_000.0,
            commission_per_fill: 0.0,
            slippage_bps: 0.0,
            sanity: Default::default(),
        },
        parallelism: 1,
    }
}

fn run_fixture(closes: &[f64], exit_after: u32) -> BacktestResult {
    let bars = bars(closes);
    let slice = TimeRange {
        start: ts(1),
        end: ts(1 + closes.len() as u32 + 1),
    };
    let mut strategy = BuyAndHoldOnce {
        bought: false,
        bars_held: 0,
        exit_after,
        on_fill_calls: 0,
    };
    let cfg = EngineConfig {
        fill_model: FillModel::NextBarOpen,
        initial_capital: 100_000.0,
        commission_per_fill: 0.0,
        slippage_bps: 0.0,
        sanity: Default::default(),
    };
    run_one(
        &mut strategy,
        &bars,
        &fixture_run(slice, 1),
        &cfg,
        IndicatorRegistry::new(),
        "art",
        "ds",
    )
    .expect("run_one")
}

#[test]
fn buy_and_hold_records_one_closed_trade_with_expected_pnl() {
    // 5 bars, entry on bar 1, exit on bar 4 (3 bars held).
    let result = run_fixture(&[50.0, 52.0, 53.0, 55.0, 60.0], 3);
    assert_eq!(result.trades.len(), 1);
    let t = &result.trades[0];
    assert_eq!(t.side, Side::Long);
    assert_eq!(t.size, 100.0);
    // NextBarOpen: entry submitted on bar 1, fills at bar 2's open (51.5).
    // Exit submitted on bar 4 (after 3 bars_held), fills at bar 5's open (59.5).
    assert!((t.entry_price - 51.5).abs() < 1e-9);
    assert!((t.exit_price - 59.5).abs() < 1e-9);
    assert!((t.pnl - (59.5 - 51.5) * 100.0).abs() < 1e-9);
}

#[test]
fn equity_curve_records_one_point_per_bar() {
    let result = run_fixture(&[50.0, 52.0, 53.0, 55.0, 60.0], 3);
    assert_eq!(result.equity.len(), 5);
    // Initial equity at bar 1 = capital (no position yet).
    assert!((result.equity[0].equity - 100_000.0).abs() < 1e-6);
}

#[test]
fn metrics_populated_after_one_trade() {
    let result = run_fixture(&[50.0, 52.0, 53.0, 55.0, 60.0], 3);
    assert_eq!(result.metrics.n_trades, 1);
    assert!(result.metrics.win_ratio > 0.99);
    assert!(result.metrics.profit_factor > 0.0);
}

#[test]
fn end_of_run_closes_open_position_with_eor_reason() {
    // Strategy never exits. Engine closes at last bar's mark.
    let result = run_fixture(&[50.0, 52.0, 53.0, 55.0, 60.0], u32::MAX);
    assert_eq!(result.trades.len(), 1);
    assert_eq!(result.trades[0].reason_out.as_deref(), Some("end_of_run"));
}

#[test]
fn determinism_identical_inputs_produce_identical_output() {
    let a = run_fixture(&[50.0, 52.0, 53.0, 55.0, 60.0], 3);
    let b = run_fixture(&[50.0, 52.0, 53.0, 55.0, 60.0], 3);
    assert_eq!(a, b);
}

#[test]
fn run_batch_executes_every_run() {
    let bars_data = bars(&[50.0, 52.0, 53.0, 55.0, 60.0]);
    let slice = TimeRange {
        start: ts(1),
        end: ts(7),
    };
    let runs = vec![
        fixture_run(slice, 1),
        fixture_run(slice, 2),
        fixture_run(slice, 3),
    ];
    let batch = fixture_batch(runs);
    let factory: Box<dyn StrategyFactory> = Box::new(|| -> Box<dyn Strategy> {
        Box::new(BuyAndHoldOnce {
            bought: false,
            bars_held: 0,
            exit_after: 3,
            on_fill_calls: 0,
        })
    });
    let results = run_batch(
        &batch,
        &bars_data,
        factory.as_ref(),
        IndicatorRegistry::new,
        "ds",
    )
    .expect("batch");
    assert_eq!(results.len(), 3);
    // Different seeds, deterministic strategy → identical metrics.
    assert_eq!(results[0].metrics, results[1].metrics);
    assert_eq!(results[1].metrics, results[2].metrics);
}

#[test]
fn run_batch_aborts_on_first_failure() {
    // Strategy that panics-via-error inside on_bar.
    struct AlwaysErrors;
    impl Sealed for AlwaysErrors {}
    impl Strategy for AlwaysErrors {
        fn metadata(&self) -> StrategyMeta {
            StrategyMeta::new("err", "0.1.0", "test", "errors")
        }
        fn on_bar(&mut self, _bar: &Bar, _ctx: &mut dyn Context) -> RtResult<()> {
            Err(engine_rt::Error::Abort("intentional".into()))
        }
    }

    let bars_data = bars(&[50.0, 52.0, 53.0]);
    let slice = TimeRange {
        start: ts(1),
        end: ts(5),
    };
    let runs = vec![fixture_run(slice, 1), fixture_run(slice, 2)];
    let batch = fixture_batch(runs);
    let factory: Box<dyn StrategyFactory> =
        Box::new(|| -> Box<dyn Strategy> { Box::new(AlwaysErrors) });

    let err = run_batch(
        &batch,
        &bars_data,
        factory.as_ref(),
        IndicatorRegistry::new,
        "ds",
    )
    .unwrap_err();
    match err {
        engine::BatchError::Run { index, source } => {
            assert_eq!(index, 0);
            assert!(format!("{source}").contains("intentional"));
        }
    }
}

#[test]
fn slippage_reduces_pnl_relative_to_zero_slip_baseline() {
    let bars_data = bars(&[50.0, 52.0, 53.0, 55.0, 60.0]);
    let slice = TimeRange {
        start: ts(1),
        end: ts(7),
    };
    let make_result = |slippage_bps: f64| -> BacktestResult {
        let mut strategy = BuyAndHoldOnce {
            bought: false,
            bars_held: 0,
            exit_after: 3,
            on_fill_calls: 0,
        };
        let cfg = EngineConfig {
            fill_model: FillModel::NextBarOpen,
            initial_capital: 100_000.0,
            commission_per_fill: 0.0,
            slippage_bps,
            sanity: Default::default(),
        };
        run_one(
            &mut strategy,
            &bars_data,
            &fixture_run(slice, 1),
            &cfg,
            IndicatorRegistry::new(),
            "art",
            "ds",
        )
        .unwrap()
    };
    let baseline = make_result(0.0);
    let slipped = make_result(0.001);
    assert!(slipped.trades[0].pnl < baseline.trades[0].pnl);
}

#[test]
fn monte_carlo_attaches_one_aggregated_stress_scenario() {
    let bars_data = bars(&[50.0, 52.0, 53.0, 55.0, 60.0, 58.0, 57.0]);
    let slice = TimeRange {
        start: ts(1),
        end: ts(10),
    };
    let mut strategy = BuyAndHoldOnce {
        bought: false,
        bars_held: 0,
        exit_after: 3,
        on_fill_calls: 0,
    };
    let cfg = EngineConfig::default();
    let run = RunSpec {
        params: serde_json::Value::Null,
        modes: vec![
            engine::Mode::Plain,
            engine::Mode::MonteCarlo {
                n: 10,
                block_size: 2,
            },
        ],
        seed: 7,
        slice,
    };
    let mut result = run_one(
        &mut strategy,
        &bars_data,
        &run,
        &cfg,
        IndicatorRegistry::new(),
        "art",
        "ds",
    )
    .unwrap();
    let factory: Box<dyn StrategyFactory> = Box::new(|| -> Box<dyn Strategy> {
        Box::new(BuyAndHoldOnce {
            bought: false,
            bars_held: 0,
            exit_after: 3,
            on_fill_calls: 0,
        })
    });
    engine::apply_modes(
        &mut result,
        factory.as_ref(),
        &IndicatorRegistry::new,
        &bars_data,
        &run,
        &cfg,
        "art",
        "ds",
    )
    .unwrap();
    let stress = result.stress.expect("monte carlo populates stress");
    assert_eq!(stress.scenarios.len(), 1);
    assert!(stress.scenarios[0].name.starts_with("monte_carlo:"));
}

#[test]
fn slippage_sweep_emits_one_scenario_per_bps_value() {
    let bars_data = bars(&[50.0, 52.0, 53.0, 55.0, 60.0]);
    let slice = TimeRange {
        start: ts(1),
        end: ts(7),
    };
    let mut strategy = BuyAndHoldOnce {
        bought: false,
        bars_held: 0,
        exit_after: 3,
        on_fill_calls: 0,
    };
    let cfg = EngineConfig::default();
    let run = RunSpec {
        params: serde_json::Value::Null,
        modes: vec![engine::Mode::Slippage {
            bps_grid: vec![0.0, 0.0005, 0.002],
        }],
        seed: 1,
        slice,
    };
    let mut result = run_one(
        &mut strategy,
        &bars_data,
        &run,
        &cfg,
        IndicatorRegistry::new(),
        "art",
        "ds",
    )
    .unwrap();
    let factory: Box<dyn StrategyFactory> = Box::new(|| -> Box<dyn Strategy> {
        Box::new(BuyAndHoldOnce {
            bought: false,
            bars_held: 0,
            exit_after: 3,
            on_fill_calls: 0,
        })
    });
    engine::apply_modes(
        &mut result,
        factory.as_ref(),
        &IndicatorRegistry::new,
        &bars_data,
        &run,
        &cfg,
        "art",
        "ds",
    )
    .unwrap();
    let stress = result.stress.expect("slippage populates stress");
    assert_eq!(stress.scenarios.len(), 3);
    assert!(stress
        .scenarios
        .iter()
        .all(|s| s.name.starts_with("slippage:")));
}

#[test]
fn regime_filter_runs_only_on_in_range_bars() {
    let bars_data = bars(&[50.0, 52.0, 53.0, 55.0, 60.0, 58.0, 57.0]);
    let slice = TimeRange {
        start: ts(1),
        end: ts(10),
    };
    let mut strategy = BuyAndHoldOnce {
        bought: false,
        bars_held: 0,
        exit_after: 3,
        on_fill_calls: 0,
    };
    let cfg = EngineConfig::default();
    let run = RunSpec {
        params: serde_json::Value::Null,
        modes: vec![engine::Mode::RegimeFilter {
            ranges: vec![TimeRange {
                start: ts(3),
                end: ts(6),
            }],
        }],
        seed: 1,
        slice,
    };
    let mut result = run_one(
        &mut strategy,
        &bars_data,
        &run,
        &cfg,
        IndicatorRegistry::new(),
        "art",
        "ds",
    )
    .unwrap();
    let factory: Box<dyn StrategyFactory> = Box::new(|| -> Box<dyn Strategy> {
        Box::new(BuyAndHoldOnce {
            bought: false,
            bars_held: 0,
            exit_after: 3,
            on_fill_calls: 0,
        })
    });
    engine::apply_modes(
        &mut result,
        factory.as_ref(),
        &IndicatorRegistry::new,
        &bars_data,
        &run,
        &cfg,
        "art",
        "ds",
    )
    .unwrap();
    let stress = result.stress.expect("regime_filter populates stress");
    assert_eq!(stress.scenarios.len(), 1);
    assert!(stress.scenarios[0].name.starts_with("regime_filter:"));
}

#[test]
fn sensitivity_sweep_emits_one_point_per_unique_value() {
    let bars_data = bars(&[50.0, 52.0, 53.0, 55.0, 60.0]);
    let slice = TimeRange {
        start: ts(1),
        end: ts(7),
    };
    let mut strategy = BuyAndHoldOnce {
        bought: false,
        bars_held: 0,
        exit_after: 3,
        on_fill_calls: 0,
    };
    let cfg = EngineConfig::default();
    let run = RunSpec {
        params: serde_json::json!({}),
        modes: vec![engine::Mode::Sensitivity {
            param: "vol_lo".into(),
            // Duplicate 1.0 to verify dedup.
            values: vec![1.0, 1.0, 2.0, 5.0],
        }],
        seed: 1,
        slice,
    };
    let mut result = run_one(
        &mut strategy,
        &bars_data,
        &run,
        &cfg,
        IndicatorRegistry::new(),
        "art",
        "ds",
    )
    .unwrap();
    let factory: Box<dyn StrategyFactory> = Box::new(|| -> Box<dyn Strategy> {
        Box::new(BuyAndHoldOnce {
            bought: false,
            bars_held: 0,
            exit_after: 3,
            on_fill_calls: 0,
        })
    });
    engine::apply_modes(
        &mut result,
        factory.as_ref(),
        &IndicatorRegistry::new,
        &bars_data,
        &run,
        &cfg,
        "art",
        "ds",
    )
    .unwrap();
    let s = result.sensitivity.expect("sensitivity populated");
    assert_eq!(s.param, "vol_lo");
    assert_eq!(s.points.len(), 3);
}

#[test]
fn regime_annotation_runs_on_long_enough_input() {
    // Construct 50 daily bars with rising closes; annotate_regimes should
    // return at least one tag (uptrend + at least one vol regime).
    let closes: Vec<f64> = (1..=50).map(|i| 100.0 + i as f64 * 0.5).collect();
    let bars: Vec<Bar> = closes
        .iter()
        .enumerate()
        .map(|(i, c)| Bar {
            symbol: "VXX".into(),
            ts: ts(1) + chrono::Duration::days(i as i64),
            resolution: Resolution::Day,
            open: *c,
            high: *c,
            low: *c,
            close: *c,
            volume: 1.0,
        })
        .collect();
    let tags = engine::annotate_regimes(&bars);
    assert!(!tags.is_empty());
}

#[test]
fn empty_dataset_returns_error() {
    let mut strategy = BuyAndHoldOnce {
        bought: false,
        bars_held: 0,
        exit_after: 3,
        on_fill_calls: 0,
    };
    let cfg = EngineConfig::default();
    let slice = TimeRange {
        start: ts(1),
        end: ts(2),
    };
    let err = run_one(
        &mut strategy,
        &[],
        &fixture_run(slice, 1),
        &cfg,
        IndicatorRegistry::new(),
        "art",
        "ds",
    )
    .unwrap_err();
    assert!(matches!(err, engine::ExecutionError::EmptyDataset));
}
