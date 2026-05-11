//! Build driver — orchestrates lint, cache lookup, and (when needed) the
//! cargo invocation that produces a strategy artifact.
//!
//! The actual `cargo` call is hidden behind the [`Cargo`] trait so the rest
//! of the pipeline can be unit-tested without spawning a compiler. The
//! production implementation [`SystemCargo`] shells out to the toolchain
//! pinned in `rust-toolchain.toml`.

use std::path::{Path, PathBuf};

use engine_rt::{RunnerVersion, RUNNER_VERSION};
use serde::{Deserialize, Serialize};

use crate::artifact_cache::{ArtifactCache, ArtifactKey, CachedArtifact};
use crate::error::BuildError;
use crate::linter::run_full_lint;
use crate::whitelist::Whitelist;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
pub struct ManifestDep {
    pub name: String,
    /// Version requirement string passed through to Cargo. The whitelist
    /// rejects unknown crates by name; this string is not interpreted here.
    pub req: String,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct StrategyManifest {
    pub name: String,
    pub version: String,
    pub dependencies: Vec<ManifestDep>,
    pub dev_dependencies: Vec<ManifestDep>,
    pub build_dependencies: Vec<ManifestDep>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum BuildOutcome {
    CacheHit(CachedArtifact),
    Compiled(CachedArtifact),
}

impl BuildOutcome {
    pub fn artifact(&self) -> &CachedArtifact {
        match self {
            BuildOutcome::CacheHit(a) | BuildOutcome::Compiled(a) => a,
        }
    }
}

pub trait Cargo {
    /// Lay out and compile a strategy crate at `project_dir`, producing a
    /// shared library at the returned path. The implementation is responsible
    /// for `cargo build` invocation, sccache configuration, and toolchain
    /// resolution.
    fn build(
        &self,
        project_dir: &Path,
        manifest: &StrategyManifest,
        source: &str,
    ) -> Result<PathBuf, BuildError>;
}

/// Production [`Cargo`] implementation that shells out to `cargo build`.
///
/// `SystemCargo` lays out a per-build Cargo project at `project_dir`
/// (writes `Cargo.toml` + `src/lib.rs`), invokes the toolchain pinned by
/// the workspace's `rust-toolchain.toml`, and returns the path to the
/// produced `cdylib`. `RUSTC_WRAPPER` is left untouched so sccache (when
/// the environment already configures it) applies automatically.
///
/// Construction requires `engine_rt_path` — the on-disk path to the
/// `engine-rt` crate the strategy depends on. In production the build
/// pipeline derives this from its own workspace layout; tests pass an
/// explicit path.
#[derive(Clone, Debug)]
pub struct SystemCargo {
    engine_rt_path: PathBuf,
    profile: BuildProfile,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum BuildProfile {
    Debug,
    #[default]
    Release,
}

impl BuildProfile {
    fn flag(self) -> Option<&'static str> {
        match self {
            BuildProfile::Debug => None,
            BuildProfile::Release => Some("--release"),
        }
    }

    fn target_subdir(self) -> &'static str {
        match self {
            BuildProfile::Debug => "debug",
            BuildProfile::Release => "release",
        }
    }
}

impl SystemCargo {
    pub fn new(engine_rt_path: impl Into<PathBuf>, profile: BuildProfile) -> Self {
        Self {
            engine_rt_path: engine_rt_path.into(),
            profile,
        }
    }

    /// Lay out a strategy crate at `project_dir` (writes `Cargo.toml` and
    /// `src/lib.rs`). Exposed so tests can verify the layout without
    /// invoking the compiler.
    pub fn lay_out_project(
        &self,
        project_dir: &Path,
        manifest: &StrategyManifest,
        source: &str,
    ) -> Result<(), BuildError> {
        std::fs::create_dir_all(project_dir.join("src"))?;
        let cargo_toml = self.render_cargo_toml(manifest);
        std::fs::write(project_dir.join("Cargo.toml"), cargo_toml)?;
        std::fs::write(project_dir.join("src").join("lib.rs"), source)?;
        Ok(())
    }

    fn render_cargo_toml(&self, manifest: &StrategyManifest) -> String {
        let mut out = String::new();
        out.push_str("[package]\n");
        out.push_str(&format!("name = \"{}\"\n", manifest.name));
        out.push_str(&format!("version = \"{}\"\n", manifest.version));
        out.push_str("edition = \"2021\"\n\n");
        out.push_str("[lib]\n");
        out.push_str("crate-type = [\"cdylib\"]\n\n");
        out.push_str("[dependencies]\n");
        out.push_str(&format!(
            "engine-rt = {{ path = \"{}\" }}\n",
            self.engine_rt_path.display(),
        ));
        for dep in &manifest.dependencies {
            if dep.name == "engine-rt" {
                // engine-rt is always injected as a path dep above; skip
                // user-supplied versions of it.
                continue;
            }
            out.push_str(&format!("{} = \"{}\"\n", dep.name, dep.req));
        }
        out
    }

    fn invoke_cargo(&self, project_dir: &Path, lib_name: &str) -> Result<PathBuf, BuildError> {
        let mut cmd = std::process::Command::new("cargo");
        cmd.arg("build").current_dir(project_dir);
        if let Some(flag) = self.profile.flag() {
            cmd.arg(flag);
        }
        let output = cmd
            .output()
            .map_err(|e| BuildError::Cargo(format!("failed to spawn cargo: {e}")))?;
        if !output.status.success() {
            return Err(BuildError::Cargo(format!(
                "cargo build failed (status {:?}):\nstdout:\n{}\nstderr:\n{}",
                output.status.code(),
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr),
            )));
        }
        let lib_file = artifact_filename(lib_name);
        let path = project_dir
            .join("target")
            .join(self.profile.target_subdir())
            .join(&lib_file);
        if !path.exists() {
            return Err(BuildError::Cargo(format!(
                "cargo build succeeded but expected artifact `{}` is missing",
                path.display()
            )));
        }
        Ok(path)
    }
}

impl Cargo for SystemCargo {
    fn build(
        &self,
        project_dir: &Path,
        manifest: &StrategyManifest,
        source: &str,
    ) -> Result<PathBuf, BuildError> {
        self.lay_out_project(project_dir, manifest, source)?;
        let lib_name = manifest.name.replace('-', "_");
        self.invoke_cargo(project_dir, &lib_name)
    }
}

fn artifact_filename(lib_name: &str) -> String {
    if cfg!(target_os = "windows") {
        format!("{lib_name}.dll")
    } else if cfg!(target_os = "macos") {
        format!("lib{lib_name}.dylib")
    } else {
        format!("lib{lib_name}.so")
    }
}

pub struct BuildDriver<C: Cargo> {
    pub cargo: C,
    pub cache: ArtifactCache,
    pub whitelist: Whitelist,
    pub runner_version: RunnerVersion,
    /// Where to lay out per-build Cargo projects. The driver creates a
    /// directory under this root keyed by artifact hash.
    pub work_root: PathBuf,
}

impl<C: Cargo> BuildDriver<C> {
    pub fn new(
        cargo: C,
        cache: ArtifactCache,
        whitelist: Whitelist,
        work_root: impl Into<PathBuf>,
    ) -> Self {
        Self {
            cargo,
            cache,
            whitelist,
            runner_version: RUNNER_VERSION,
            work_root: work_root.into(),
        }
    }

    pub fn build(
        &self,
        source: &str,
        manifest: &StrategyManifest,
    ) -> Result<BuildOutcome, BuildError> {
        // 1. Lint
        let report = run_full_lint(source, manifest, &self.whitelist);
        if !report.is_clean() {
            if !report.source_violations.is_empty() {
                return Err(BuildError::SourceLint(report.merged_message()));
            }
            return Err(BuildError::ManifestLint(report.merged_message()));
        }

        // 2. Cache lookup
        let key = ArtifactKey::from_inputs(source, manifest, self.runner_version);
        if let Some(cached) = self.cache.lookup(key) {
            return Ok(BuildOutcome::CacheHit(cached));
        }

        // 3. Lay out + cargo build via injected impl
        let project_dir = self.work_root.join(key.as_hex());
        std::fs::create_dir_all(&project_dir)?;
        let library_path = self.cargo.build(&project_dir, manifest, source)?;

        // 4. Cache + return
        let stored =
            self.cache
                .store(key, library_path, self.runner_version, source.len() as u64)?;
        Ok(BuildOutcome::Compiled(stored))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    use std::fs;
    use std::path::PathBuf;

    struct StubCargo {
        calls: RefCell<usize>,
        out_dir: PathBuf,
    }

    impl StubCargo {
        fn new(out_dir: PathBuf) -> Self {
            Self {
                calls: RefCell::new(0),
                out_dir,
            }
        }
    }

    impl Cargo for StubCargo {
        fn build(
            &self,
            _project_dir: &Path,
            manifest: &StrategyManifest,
            _source: &str,
        ) -> Result<PathBuf, BuildError> {
            *self.calls.borrow_mut() += 1;
            // Place a fake library under out_dir keyed by the manifest name.
            let p = self.out_dir.join(format!("lib{}.so", manifest.name));
            fs::write(&p, b"fake compiled artifact")?;
            Ok(p)
        }
    }

    fn tmpdir(label: &str) -> PathBuf {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let pid = std::process::id();
        let dir = std::env::temp_dir().join(format!("strategy-gpt-{label}-{pid}-{now}"));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn make_driver(label: &str) -> (BuildDriver<StubCargo>, PathBuf) {
        let cache_root = tmpdir(&format!("{label}-cache"));
        let work_root = tmpdir(&format!("{label}-work"));
        let out_dir = tmpdir(&format!("{label}-out"));
        let driver = BuildDriver::new(
            StubCargo::new(out_dir.clone()),
            ArtifactCache::new(&cache_root),
            Whitelist::parse_toml(include_str!("../whitelist.toml")).unwrap(),
            &work_root,
        );
        (driver, out_dir)
    }

    fn ok_manifest() -> StrategyManifest {
        StrategyManifest {
            name: "ok_strategy".into(),
            version: "0.1.0".into(),
            dependencies: vec![ManifestDep {
                name: "engine-rt".into(),
                req: "*".into(),
            }],
            dev_dependencies: vec![],
            build_dependencies: vec![],
        }
    }

    const OK_SOURCE: &str = r#"
        use engine_rt::{Bar, Context};
        pub fn helper(x: i32) -> i32 { x + 1 }
    "#;

    #[test]
    fn happy_path_compiles_and_stores() {
        let (driver, _out) = make_driver("happy");
        let outcome = driver.build(OK_SOURCE, &ok_manifest()).unwrap();
        assert!(matches!(outcome, BuildOutcome::Compiled(_)));
        assert_eq!(*driver.cargo.calls.borrow(), 1);
    }

    #[test]
    fn second_call_is_cache_hit() {
        let (driver, _out) = make_driver("cache");
        let _ = driver.build(OK_SOURCE, &ok_manifest()).unwrap();
        let outcome = driver.build(OK_SOURCE, &ok_manifest()).unwrap();
        assert!(matches!(outcome, BuildOutcome::CacheHit(_)));
        assert_eq!(*driver.cargo.calls.borrow(), 1, "compile must not re-run");
    }

    #[test]
    fn source_lint_blocks_compile() {
        let (driver, _out) = make_driver("source-lint");
        let bad = "pub unsafe fn evil() {}";
        let err = driver.build(bad, &ok_manifest()).unwrap_err();
        assert!(matches!(err, BuildError::SourceLint(_)));
        assert_eq!(*driver.cargo.calls.borrow(), 0);
    }

    #[test]
    fn manifest_lint_blocks_compile() {
        let (driver, _out) = make_driver("manifest-lint");
        let manifest = StrategyManifest {
            name: "evil".into(),
            version: "0.1.0".into(),
            dependencies: vec![ManifestDep {
                name: "tokio".into(),
                req: "*".into(),
            }],
            dev_dependencies: vec![],
            build_dependencies: vec![],
        };
        let err = driver.build(OK_SOURCE, &manifest).unwrap_err();
        assert!(matches!(err, BuildError::ManifestLint(_)));
        assert_eq!(*driver.cargo.calls.borrow(), 0);
    }

    #[test]
    fn different_source_takes_separate_artifacts() {
        let (driver, _out) = make_driver("distinct");
        let _ = driver.build(OK_SOURCE, &ok_manifest()).unwrap();
        let alt_source = format!("{OK_SOURCE}\n// alt\n");
        let outcome = driver.build(&alt_source, &ok_manifest()).unwrap();
        assert!(matches!(outcome, BuildOutcome::Compiled(_)));
        assert_eq!(*driver.cargo.calls.borrow(), 2);
    }

    // --- SystemCargo unit tests (don't invoke real cargo) --------------

    fn engine_rt_workspace_path() -> PathBuf {
        // CARGO_MANIFEST_DIR is `crates/build-pipeline/`; engine-rt lives
        // at the workspace sibling level.
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .join("engine-rt")
    }

    #[test]
    fn system_cargo_lays_out_project_with_correct_manifest_and_source() {
        let project_dir = tmpdir("syscargo-layout");
        let sc = SystemCargo::new(engine_rt_workspace_path(), BuildProfile::Release);
        let manifest = ok_manifest();
        sc.lay_out_project(&project_dir, &manifest, OK_SOURCE)
            .unwrap();

        let cargo_toml = fs::read_to_string(project_dir.join("Cargo.toml")).unwrap();
        assert!(cargo_toml.contains("name = \"ok_strategy\""));
        assert!(cargo_toml.contains("crate-type = [\"cdylib\"]"));
        assert!(
            cargo_toml.contains("engine-rt = { path ="),
            "Cargo.toml must inject engine-rt as a path dep"
        );

        let lib_rs = fs::read_to_string(project_dir.join("src/lib.rs")).unwrap();
        assert_eq!(lib_rs, OK_SOURCE);
    }

    #[test]
    fn system_cargo_skips_user_supplied_engine_rt_dep() {
        // The user-emitted manifest references `engine-rt = "*"`; SystemCargo
        // must not emit two entries for it.
        let project_dir = tmpdir("syscargo-engine-rt-dedup");
        let sc = SystemCargo::new(engine_rt_workspace_path(), BuildProfile::Release);
        let manifest = StrategyManifest {
            name: "deduped".into(),
            version: "0.1.0".into(),
            dependencies: vec![
                ManifestDep {
                    name: "engine-rt".into(),
                    req: "*".into(),
                },
                ManifestDep {
                    name: "chrono".into(),
                    req: "0.4".into(),
                },
            ],
            dev_dependencies: vec![],
            build_dependencies: vec![],
        };
        sc.lay_out_project(&project_dir, &manifest, OK_SOURCE)
            .unwrap();
        let cargo_toml = fs::read_to_string(project_dir.join("Cargo.toml")).unwrap();
        // Count dependency lines that start with `engine-rt =`. The path
        // string itself contains the substring `engine-rt` so a raw substring
        // count would over-report; we want exactly one *dependency key*.
        let engine_rt_lines = cargo_toml
            .lines()
            .filter(|line| line.trim_start().starts_with("engine-rt ="))
            .count();
        assert_eq!(
            engine_rt_lines, 1,
            "exactly one engine-rt dependency line expected"
        );
        assert!(cargo_toml.contains("chrono = \"0.4\""));
    }

    /// Full integration: real `cargo build` of a minimal strategy. Slow
    /// (compiles `engine-rt` + deps from scratch into the temp project).
    /// Marked `#[ignore]` so default `cargo test` stays fast; run with
    /// `cargo test -p build-pipeline -- --ignored`.
    #[test]
    #[ignore]
    fn system_cargo_compiles_minimal_strategy_end_to_end() {
        let project_dir = tmpdir("syscargo-real");
        let sc = SystemCargo::new(engine_rt_workspace_path(), BuildProfile::Release);
        let manifest = StrategyManifest {
            name: "minimal_real".into(),
            version: "0.1.0".into(),
            dependencies: vec![ManifestDep {
                name: "engine-rt".into(),
                req: "*".into(),
            }],
            dev_dependencies: vec![],
            build_dependencies: vec![],
        };
        let source = r#"
            use engine_rt::{strategy_entry, Bar, Context, Fill, Result, Sealed, Strategy, StrategyMeta};

            #[derive(Default)]
            pub struct M;
            impl Sealed for M {}
            impl Strategy for M {
                fn metadata(&self) -> StrategyMeta {
                    StrategyMeta::new("m", "0.1.0", "test", "minimal")
                }
                fn on_bar(&mut self, _bar: &Bar, _ctx: &mut dyn Context) -> Result<()> {
                    Ok(())
                }
            }
            fn make() -> Box<dyn Strategy> { Box::<M>::default() }
            strategy_entry!(make);
        "#;
        let path = <SystemCargo as Cargo>::build(&sc, &project_dir, &manifest, source).unwrap();
        assert!(path.exists(), "expected cdylib at {}", path.display());
    }
}
