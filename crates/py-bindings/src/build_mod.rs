//! PyO3 bindings for `build_pipeline::BuildDriver`.
//!
//! Surface:
//! - `PyBuildPipeline(cache_root, work_root, engine_rt_path, whitelist_path,
//!    profile = "release")` — open a driver backed by [`SystemCargo`].
//! - `lint(source, manifest_json) -> str` — run source + manifest lint only;
//!   returns JSON `{ "ok": bool, "source_violations": [...],
//!   "manifest_violations": [...] }`.
//! - `build(source, manifest_json) -> str` — run the full pipeline
//!   (lint → cache lookup → cargo build). Returns a tagged JSON envelope so
//!   the Python tester can branch on `kind` without parsing exception
//!   messages:
//!   - success: `{ "ok": { "kind": "cache_hit" | "compiled",
//!     "artifact": { "key", "library_path", "runner_version",
//!     "source_size_bytes" } } }`
//!   - failure: `{ "err": { "kind": "source_lint" | "manifest_lint" | ...,
//!     "message": "..." } }`
//!
//! Returning errors as data (rather than `PyErr`) lets the Tester surface a
//! `rejected: build_failed` decision with the structured diagnostic still
//! attached, matching `tester::compile-and-lint-validation`.

use std::path::PathBuf;

use build_pipeline::driver::{BuildProfile, ManifestDep, SystemCargo};
use build_pipeline::{
    lint_manifest, lint_source, ArtifactCache, BuildDriver, BuildError, BuildOutcome,
    StrategyManifest, Whitelist,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde::Serialize;

use crate::{io_err, json_err};

#[pyclass(
    module = "strategy_gpt_native.build",
    name = "BuildPipeline",
    unsendable
)]
pub struct PyBuildPipeline {
    driver: BuildDriver<SystemCargo>,
}

#[pymethods]
impl PyBuildPipeline {
    /// Open a driver.
    ///
    /// `cache_root` — directory the [`ArtifactCache`] keys cached artifacts
    /// under.
    /// `work_root` — directory the per-build Cargo projects are laid out in.
    /// `engine_rt_path` — on-disk path to the workspace's `engine-rt` crate
    /// (the strategy crate depends on it as a path dep).
    /// `whitelist_path` — path to the build pipeline's `whitelist.toml`.
    /// `profile` — `"release"` (default) or `"debug"`.
    #[new]
    #[pyo3(signature = (cache_root, work_root, engine_rt_path, whitelist_path, profile = "release"))]
    fn new(
        cache_root: &str,
        work_root: &str,
        engine_rt_path: &str,
        whitelist_path: &str,
        profile: &str,
    ) -> PyResult<Self> {
        let whitelist = Whitelist::from_file(whitelist_path).map_err(io_err)?;
        let cache = ArtifactCache::new(PathBuf::from(cache_root));
        let build_profile = match profile {
            "release" => BuildProfile::Release,
            "debug" => BuildProfile::Debug,
            other => {
                return Err(PyValueError::new_err(format!(
                    "unknown profile `{other}`; expected one of release, debug"
                )))
            }
        };
        let cargo = SystemCargo::new(PathBuf::from(engine_rt_path), build_profile);
        let driver = BuildDriver::new(cargo, cache, whitelist, PathBuf::from(work_root));
        Ok(Self { driver })
    }

    /// Lint only (no cache lookup, no cargo invocation). Returns a JSON
    /// `LintReport` envelope so Python callers can inspect violations
    /// without throwing.
    fn lint(&self, source: &str, manifest_json: &str) -> PyResult<String> {
        let manifest: ManifestPayload = serde_json::from_str(manifest_json).map_err(json_err)?;
        let manifest = manifest.into_inner();
        let source_violations = lint_source(source);
        let manifest_violations = lint_manifest(&manifest, &self.driver.whitelist);
        let report = LintReportPayload {
            ok: source_violations.is_empty() && manifest_violations.is_empty(),
            source_violations,
            manifest_violations,
        };
        serde_json::to_string(&report).map_err(|e| PyValueError::new_err(format!("{e}")))
    }

    /// Full pipeline: lint → cache lookup → cargo build. Errors are returned
    /// as tagged data inside the JSON envelope (see module docs).
    fn build(&self, source: &str, manifest_json: &str) -> PyResult<String> {
        let manifest: ManifestPayload = serde_json::from_str(manifest_json).map_err(json_err)?;
        let manifest = manifest.into_inner();
        let envelope = match self.driver.build(source, &manifest) {
            Ok(outcome) => BuildEnvelope::Ok {
                ok: outcome_payload(outcome),
            },
            Err(err) => BuildEnvelope::Err {
                err: error_payload(err),
            },
        };
        serde_json::to_string(&envelope).map_err(|e| PyValueError::new_err(format!("{e}")))
    }
}

#[derive(serde::Deserialize)]
struct ManifestPayload {
    name: String,
    version: String,
    #[serde(default)]
    dependencies: Vec<ManifestDepPayload>,
    #[serde(default)]
    dev_dependencies: Vec<ManifestDepPayload>,
    #[serde(default)]
    build_dependencies: Vec<ManifestDepPayload>,
}

#[derive(serde::Deserialize)]
struct ManifestDepPayload {
    name: String,
    req: String,
}

impl ManifestPayload {
    fn into_inner(self) -> StrategyManifest {
        StrategyManifest {
            name: self.name,
            version: self.version,
            dependencies: self.dependencies.into_iter().map(into_dep).collect(),
            dev_dependencies: self.dev_dependencies.into_iter().map(into_dep).collect(),
            build_dependencies: self.build_dependencies.into_iter().map(into_dep).collect(),
        }
    }
}

fn into_dep(d: ManifestDepPayload) -> ManifestDep {
    ManifestDep {
        name: d.name,
        req: d.req,
    }
}

#[derive(Serialize)]
struct LintReportPayload {
    ok: bool,
    source_violations: Vec<String>,
    manifest_violations: Vec<String>,
}

#[derive(Serialize)]
#[serde(untagged)]
enum BuildEnvelope {
    Ok { ok: OkPayload },
    Err { err: ErrPayload },
}

#[derive(Serialize)]
struct OkPayload {
    kind: &'static str,
    artifact: ArtifactPayload,
}

#[derive(Serialize)]
struct ArtifactPayload {
    key: String,
    library_path: String,
    runner_version: engine_rt::RunnerVersion,
    source_size_bytes: u64,
}

#[derive(Serialize)]
struct ErrPayload {
    kind: &'static str,
    message: String,
}

fn outcome_payload(outcome: BuildOutcome) -> OkPayload {
    let (kind, artifact) = match outcome {
        BuildOutcome::CacheHit(a) => ("cache_hit", a),
        BuildOutcome::Compiled(a) => ("compiled", a),
    };
    OkPayload {
        kind,
        artifact: ArtifactPayload {
            key: artifact.key.as_hex(),
            library_path: artifact.library_path.display().to_string(),
            runner_version: artifact.runner_version,
            source_size_bytes: artifact.source_size_bytes,
        },
    }
}

fn error_payload(err: BuildError) -> ErrPayload {
    let kind = match &err {
        BuildError::SourceLint(_) => "source_lint",
        BuildError::ManifestLint(_) => "manifest_lint",
        BuildError::Whitelist(_) => "whitelist",
        BuildError::Io(_) => "io",
        BuildError::Cargo(_) => "cargo",
        BuildError::ArtifactCache(_) => "artifact_cache",
        BuildError::Migration(_) => "migration",
        BuildError::ParamsSchema(_) => "params_schema",
    };
    ErrPayload {
        kind,
        message: err.to_string(),
    }
}
