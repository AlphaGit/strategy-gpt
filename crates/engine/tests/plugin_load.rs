//! Integration test: build the `example-strategy` cdylib, load it through
//! [`engine::StrategyPlugin`], drive its lifecycle, and verify metadata +
//! ABI compatibility.
//!
//! The test invokes `cargo build -p example-strategy` so it remains
//! self-contained — running `cargo test -p engine` from a clean tree
//! produces the artifact on demand.

use std::path::PathBuf;
use std::process::Command;

use engine::plugin::StrategyPlugin;
use engine_rt::RUNNER_VERSION;

/// Build `example-strategy` and return the absolute path to its cdylib.
fn build_example_strategy() -> PathBuf {
    let status = Command::new(env!("CARGO"))
        .args(["build", "-p", "example-strategy"])
        .status()
        .expect("invoking `cargo build -p example-strategy`");
    assert!(status.success(), "cargo build -p example-strategy failed");
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    // CARGO_MANIFEST_DIR is `crates/engine/`; workspace target is at
    // `crates/target/` (one up + target/).
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

#[test]
fn plugin_loads_example_strategy_and_returns_metadata() {
    let dylib = build_example_strategy();
    let plugin = StrategyPlugin::load(&dylib).expect("plugin load");
    assert_eq!(plugin.abi_major(), RUNNER_VERSION.major);

    let mut instance = plugin.create();
    let meta = instance.strategy_mut().metadata();
    assert_eq!(meta.name, "example_noop");
    assert_eq!(meta.runner_version, RUNNER_VERSION);
}

#[test]
fn plugin_create_drop_cycle_is_idempotent() {
    // Re-creating + dropping in a loop must not leak symbols or crash the
    // library. The plugin's drop symbol owns its own deallocation; we just
    // exercise the path.
    let dylib = build_example_strategy();
    let plugin = StrategyPlugin::load(&dylib).expect("plugin load");
    for _ in 0..8 {
        let mut instance = plugin.create();
        let _meta = instance.strategy_mut().metadata();
        // Instance drops at end of each loop iteration.
    }
}

#[test]
fn plugin_load_missing_path_returns_open_error() {
    let err = StrategyPlugin::load("/tmp/strategy-gpt-does-not-exist.dylib").unwrap_err();
    assert!(
        matches!(err, engine::plugin::PluginError::Open { .. }),
        "expected Open error, got {err:?}"
    );
}
