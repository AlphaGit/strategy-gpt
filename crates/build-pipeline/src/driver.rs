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

/// Production [`Cargo`] implementation that shells out. Not implemented yet —
/// will be wired alongside the registry mirror (task 3.3) and integration
/// tests (task 3.7). The build pipeline's logic is covered by unit tests
/// against mock [`Cargo`] instances.
#[derive(Clone, Debug, Default)]
pub struct SystemCargo;

impl Cargo for SystemCargo {
    fn build(
        &self,
        _project_dir: &Path,
        _manifest: &StrategyManifest,
        _source: &str,
    ) -> Result<PathBuf, BuildError> {
        Err(BuildError::Cargo(
            "SystemCargo not yet implemented; pending registry-mirror landing (task 3.3) and \
             real cargo invocation (task 3.4 follow-up)"
                .into(),
        ))
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
}
