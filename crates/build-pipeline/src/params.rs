//! Declared parameter schema introspection for strategy artifacts.
//!
//! Every strategy crate ships a `params_schema.json` at the crate root (see
//! `engine-rt/PROMPT_API.md` §4). The build pipeline reads it during layout,
//! validates it against the [`engine_rt::ParamSchema`] shape, and persists
//! it alongside the cached artifact so the tester can introspect parameters
//! from a compiled artifact without re-reading the source tree.

use std::path::{Path, PathBuf};

use engine_rt::{ParamSchema, ParamSchemaError};

use crate::artifact_cache::ArtifactCache;
use crate::error::BuildError;

/// File name strategies use at the crate root.
pub const PARAMS_SCHEMA_FILE: &str = "params_schema.json";

/// File name we use to mirror the schema inside the artifact cache.
pub const PARAMS_SCHEMA_CACHE_FILE: &str = "params_schema.json";

/// Read and validate the `params_schema.json` for the strategy laid out
/// at `project_dir`. Returns the validated schema; surfaces a
/// [`BuildError::ParamsSchema`] when the file is missing or malformed.
pub fn read_params_schema(project_dir: &Path) -> Result<ParamSchema, BuildError> {
    let path = project_dir.join(PARAMS_SCHEMA_FILE);
    let src = std::fs::read_to_string(&path).map_err(|e| {
        BuildError::ParamsSchema(format!(
            "{PARAMS_SCHEMA_FILE} not found at {}: {e}",
            path.display()
        ))
    })?;
    ParamSchema::parse_json(&src).map_err(map_schema_err)
}

/// Parse a JSON-shaped param schema directly. Useful when the LLM emit is
/// in memory rather than on disk.
pub fn parse_params_schema(src: &str) -> Result<ParamSchema, BuildError> {
    ParamSchema::parse_json(src).map_err(map_schema_err)
}

fn map_schema_err(e: ParamSchemaError) -> BuildError {
    BuildError::ParamsSchema(format!("{e}"))
}

/// Path the cache uses to mirror the param schema next to the artifact's
/// metadata file.
pub fn cache_schema_path(cache: &ArtifactCache, key: crate::ArtifactKey) -> PathBuf {
    cache.artifact_dir(key).join(PARAMS_SCHEMA_CACHE_FILE)
}

/// Persist the schema in the cache directory alongside `artifact.json`.
/// Idempotent — overwriting is fine because the schema content is bound
/// to the same content hash as the artifact key.
pub fn write_cached_schema(
    cache: &ArtifactCache,
    key: crate::ArtifactKey,
    schema: &ParamSchema,
) -> Result<(), BuildError> {
    let bytes = serde_json::to_vec_pretty(schema).map_err(|e| {
        BuildError::ArtifactCache(format!("params_schema serialization failed: {e}"))
    })?;
    std::fs::write(cache_schema_path(cache, key), bytes)?;
    Ok(())
}

/// Read the schema cached for `key`. Returns `None` when the cache entry
/// has no schema (e.g. legacy artifacts produced before this surface
/// existed). Errors only on JSON corruption.
pub fn declared_param_schema(
    cache: &ArtifactCache,
    key: crate::ArtifactKey,
) -> Result<Option<ParamSchema>, BuildError> {
    let path = cache_schema_path(cache, key);
    let src = match std::fs::read_to_string(&path) {
        Ok(s) => s,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(BuildError::ArtifactCache(format!("read schema: {e}"))),
    };
    let schema = ParamSchema::parse_json(&src).map_err(map_schema_err)?;
    Ok(Some(schema))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn tempdir(label: &str) -> PathBuf {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let pid = std::process::id();
        let dir = std::env::temp_dir().join(format!("strategy-gpt-{label}-{pid}-{now}"));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    const OK_SCHEMA: &str = r#"{
        "schema_version": 1,
        "params": [
            {"name": "vol_lo", "kind": "f64", "min": 0.0, "max": 1.0, "default": 0.01}
        ]
    }"#;

    #[test]
    fn reads_valid_schema_from_project_dir() {
        let dir = tempdir("ok-schema");
        fs::write(dir.join("params_schema.json"), OK_SCHEMA).unwrap();
        let s = read_params_schema(&dir).unwrap();
        assert_eq!(s.params.len(), 1);
        assert_eq!(s.params[0].name, "vol_lo");
    }

    #[test]
    fn missing_file_surfaces_params_schema_error() {
        let dir = tempdir("missing-schema");
        let err = read_params_schema(&dir).unwrap_err();
        assert!(matches!(err, BuildError::ParamsSchema(_)));
    }

    #[test]
    fn malformed_json_surfaces_params_schema_error() {
        let dir = tempdir("bad-schema");
        fs::write(dir.join("params_schema.json"), "not json").unwrap();
        let err = read_params_schema(&dir).unwrap_err();
        assert!(matches!(err, BuildError::ParamsSchema(_)));
    }

    #[test]
    fn cache_round_trip_via_key() {
        use crate::driver::{ManifestDep, StrategyManifest};
        use crate::ArtifactKey;
        use engine_rt::RUNNER_VERSION;

        let dir = tempdir("cache-roundtrip");
        let cache = ArtifactCache::new(&dir);
        let manifest = StrategyManifest {
            name: "s".into(),
            version: "0.1.0".into(),
            dependencies: vec![ManifestDep {
                name: "engine-rt".into(),
                req: "*".into(),
            }],
            dev_dependencies: vec![],
            build_dependencies: vec![],
        };
        let key = ArtifactKey::from_inputs("src", &manifest, RUNNER_VERSION);
        fs::create_dir_all(cache.artifact_dir(key)).unwrap();
        let s = ParamSchema::parse_json(OK_SCHEMA).unwrap();
        write_cached_schema(&cache, key, &s).unwrap();
        let back = declared_param_schema(&cache, key).unwrap().unwrap();
        assert_eq!(back, s);
    }

    #[test]
    fn declared_param_schema_returns_none_when_missing() {
        use crate::driver::{ManifestDep, StrategyManifest};
        use crate::ArtifactKey;
        use engine_rt::RUNNER_VERSION;

        let dir = tempdir("cache-missing");
        let cache = ArtifactCache::new(&dir);
        let manifest = StrategyManifest {
            name: "s".into(),
            version: "0.1.0".into(),
            dependencies: vec![ManifestDep {
                name: "engine-rt".into(),
                req: "*".into(),
            }],
            dev_dependencies: vec![],
            build_dependencies: vec![],
        };
        let key = ArtifactKey::from_inputs("src", &manifest, RUNNER_VERSION);
        fs::create_dir_all(cache.artifact_dir(key)).unwrap();
        assert!(declared_param_schema(&cache, key).unwrap().is_none());
    }
}
