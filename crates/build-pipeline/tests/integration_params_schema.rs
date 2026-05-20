//! End-to-end integration check that the real `params_schema.json`
//! files shipped with `vxx-strategy` and `example-strategy` parse via
//! the build pipeline's introspection surface against engine-rt's
//! `ParamSchema` validator.
//!
//! Run alongside the unit suite: `cargo test -p build-pipeline --test
//! integration_params_schema`.

use std::path::PathBuf;

use build_pipeline::driver::{BuildProfile, ManifestDep, SystemCargo};
use build_pipeline::{
    declared_param_schema, read_params_schema, ArtifactCache, BuildDriver, BuildOutcome,
    StrategyManifest, Whitelist,
};

fn crate_root(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join(name)
}

#[test]
fn vxx_strategy_params_schema_parses() {
    let schema = read_params_schema(&crate_root("vxx-strategy")).expect("vxx schema must parse");
    let names: Vec<_> = schema.names().collect();
    assert_eq!(names, vec!["vol_lo", "vol_hi", "size", "symbol"]);
    let vol_lo = schema.get("vol_lo").expect("vol_lo declared");
    assert_eq!(vol_lo.kind, engine_rt::ParamKind::F64);
    assert_eq!(vol_lo.min, Some(0.001));
    assert_eq!(vol_lo.max, Some(0.05));
    let symbol = schema.get("symbol").expect("symbol declared");
    assert_eq!(symbol.kind, engine_rt::ParamKind::String);
    assert!(symbol.min.is_none());
    assert!(symbol.max.is_none());
}

fn tempdir(label: &str) -> PathBuf {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let pid = std::process::id();
    let dir = std::env::temp_dir().join(format!("strategy-gpt-itest-{label}-{pid}-{now}"));
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

/// Full Phase-A flow: build a minimal strategy with params_schema.json,
/// confirm the schema lands in the artifact cache, then introspect via
/// `declared_param_schema`. Marked `#[ignore]` so default test runs
/// stay fast; invoke explicitly with `--ignored`.
#[test]
#[ignore]
fn build_with_params_schema_e2e_via_real_cargo() {
    let cache_root = tempdir("cache");
    let work_root = tempdir("work");
    let engine_rt = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .join("engine-rt");
    let driver = BuildDriver::new(
        SystemCargo::new(engine_rt, BuildProfile::Release),
        ArtifactCache::new(&cache_root),
        Whitelist::parse_toml(include_str!("../whitelist.toml")).unwrap(),
        &work_root,
    );

    let manifest = StrategyManifest {
        name: "phase_a_smoke".into(),
        version: "0.1.0".into(),
        dependencies: vec![
            ManifestDep {
                name: "engine-rt".into(),
                req: "*".into(),
            },
            ManifestDep {
                name: "serde".into(),
                req: "*".into(),
            },
            ManifestDep {
                name: "serde_json".into(),
                req: "*".into(),
            },
        ],
        dev_dependencies: vec![],
        build_dependencies: vec![],
    };

    let source = r#"
        use engine_rt::{strategy_entry, Bar, Context, Fill, Result, Sealed, Strategy, StrategyMeta};

        #[derive(Default)]
        pub struct S;
        impl Sealed for S {}
        impl Strategy for S {
            fn metadata(&self) -> StrategyMeta {
                StrategyMeta::new("phase_a_smoke", "0.1.0", "test", "")
            }
            fn on_bar(&mut self, _bar: &Bar, _ctx: &mut dyn Context) -> Result<()> {
                Ok(())
            }
        }
        fn make() -> Box<dyn Strategy> { Box::<S>::default() }
        strategy_entry!(make);
    "#;

    let schema_json = r#"{
        "schema_version": 1,
        "params": [
            {"name": "thr", "kind": "f64", "min": 0.0, "max": 1.0, "default": 0.5},
            {"name": "flag", "kind": "bool", "default": true}
        ]
    }"#;

    let outcome = driver
        .build_with_params_schema(source, &manifest, schema_json)
        .expect("real cargo build must succeed");
    let key = match outcome {
        BuildOutcome::Compiled(c) | BuildOutcome::CacheHit(c) => {
            assert!(
                c.library_path.exists(),
                "cdylib must exist at {:?}",
                c.library_path
            );
            c.key
        }
    };

    let schema = declared_param_schema(&driver.cache, key)
        .expect("cache read")
        .expect("schema present");
    assert_eq!(schema.params.len(), 2);
    assert_eq!(schema.get("thr").unwrap().min, Some(0.0));
    assert!(matches!(
        schema.get("flag").unwrap().kind,
        engine_rt::ParamKind::Bool
    ));

    // Second build = cache hit, no extra cargo invocation.
    let again = driver
        .build_with_params_schema(source, &manifest, schema_json)
        .unwrap();
    assert!(matches!(again, BuildOutcome::CacheHit(_)));
}

#[test]
fn example_strategy_params_schema_parses_empty() {
    let schema =
        read_params_schema(&crate_root("example-strategy")).expect("example schema must parse");
    assert!(schema.params.is_empty());
    assert_eq!(
        schema.schema_version,
        engine_rt::ParamSchema::SCHEMA_VERSION
    );
}
