//! Integration test for `PluginFactory` + plugin-backed `apply_modes`.
//!
//! Builds the `example-strategy` cdylib, wraps it in `PluginFactory`, and
//! drives `run_batch` + `apply_modes` end-to-end. The fixture strategy is a
//! no-op, so we assert on plumbing (correct number of runs, mode-derived
//! sub-results present, no leaks across the create/drop cycle) rather than
//! trade-level outcomes.

use std::path::PathBuf;
use std::process::Command;
use std::sync::Arc;

use chrono::{TimeZone, Utc};
use engine::executor::StrategyFactory;
use engine::indicators::baseline_registry;
use engine::plugin::{PluginFactory, StrategyPlugin};
use engine::spec::{
    BatchSpec, DatasetRef, EngineConfig, Mode, RunSpec, StrategyArtifactRef, TimeRange,
};
use engine::{annotate_regimes, apply_modes, run_batch};
use engine_rt::{Bar, Resolution};

fn build_example_strategy() -> PathBuf {
    let status = Command::new(env!("CARGO"))
        .args(["build", "-p", "example-strategy"])
        .status()
        .expect("invoking `cargo build -p example-strategy`");
    assert!(status.success(), "cargo build -p example-strategy failed");
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let workspace_target = manifest_dir
        .parent()
        .expect("engine crate has a parent dir")
        .join("target");
    let dylib_name = if cfg!(target_os = "windows") {
        "example_strategy.dll"
    } else if cfg!(target_os = "macos") {
        "libexample_strategy.dylib"
    } else {
        "libexample_strategy.so"
    };
    let path = workspace_target.join("debug").join(dylib_name);
    assert!(
        path.exists(),
        "expected example-strategy artifact at {}",
        path.display()
    );
    path
}

fn day_bars(n: usize) -> Vec<Bar> {
    (0..n)
        .map(|i| Bar {
            symbol: "VXX".into(),
            ts: Utc.with_ymd_and_hms(2024, 1, 1, 0, 0, 0).unwrap()
                + chrono::Duration::days(i as i64),
            resolution: Resolution::Day,
            open: 100.0 + i as f64,
            high: 101.0 + i as f64,
            low: 99.0 + i as f64,
            close: 100.0 + i as f64,
            volume: 1_000.0,
        })
        .collect()
}

#[test]
fn plugin_factory_drives_run_batch_and_apply_modes() {
    let dylib = build_example_strategy();
    let plugin = Arc::new(StrategyPlugin::load(&dylib).expect("plugin load"));
    let factory = PluginFactory(Arc::clone(&plugin));

    let bars = day_bars(60);
    let slice = TimeRange {
        start: bars.first().unwrap().ts,
        end: bars.last().unwrap().ts + chrono::Duration::days(1),
    };
    let run = RunSpec {
        params: serde_json::json!({}),
        modes: vec![
            Mode::Plain,
            Mode::Slippage {
                bps_grid: vec![0.0, 0.0005],
            },
        ],
        seed: 42,
        slice,
    };
    let spec = BatchSpec {
        strategy: StrategyArtifactRef("example_noop_artifact".into()),
        dataset: DatasetRef("manifest_hash".into()),
        runs: vec![run.clone()],
        engine: EngineConfig::default(),
        parallelism: 1,
    };

    let mut results =
        run_batch(&spec, &bars, &factory, baseline_registry, "manifest_hash").expect("run_batch");
    assert_eq!(results.len(), 1, "one run per spec");

    apply_modes(
        &mut results[0],
        &factory,
        &baseline_registry,
        &bars,
        &run,
        &spec.engine,
        &spec.strategy.0,
        "manifest_hash",
    )
    .expect("apply_modes");

    let stress = results[0]
        .stress
        .as_ref()
        .expect("slippage produced stress");
    assert_eq!(
        stress.scenarios.len(),
        2,
        "two slippage scenarios expected (0 bps, 5 bps)"
    );

    results[0].regimes = annotate_regimes(&bars);
    assert!(
        !results[0].regimes.is_empty(),
        "regime annotation should emit at least one run over 60 bars"
    );
}

#[test]
fn plugin_factory_make_produces_independent_instances() {
    let dylib = build_example_strategy();
    let plugin = Arc::new(StrategyPlugin::load(&dylib).expect("plugin load"));
    let factory = PluginFactory(Arc::clone(&plugin));

    // Each `make()` must yield a fresh strategy: dropping one MUST NOT affect
    // others. We exercise the create/drop cycle several times to surface any
    // double-free / leak.
    for _ in 0..16 {
        let s = factory.make();
        let meta = s.metadata();
        assert_eq!(meta.name, "example_noop");
    }
}
