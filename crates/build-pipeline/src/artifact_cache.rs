//! Content-addressed strategy artifact cache.
//!
//! Key: `blake3(source || "\n" || canonical_manifest || "\n" || runner_version)`.
//! Same inputs = same key = artifact reused without recompiling. Different
//! inputs = new key = fresh compile.

use std::path::{Path, PathBuf};

use engine_rt::RunnerVersion;
use serde::{Deserialize, Serialize};

use crate::driver::StrategyManifest;
use crate::error::BuildError;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Hash, Serialize, Deserialize)]
pub struct ArtifactKey([u8; 32]);

impl ArtifactKey {
    pub fn from_inputs(
        source: &str,
        manifest: &StrategyManifest,
        runner_version: RunnerVersion,
    ) -> Self {
        let mut hasher = blake3::Hasher::new();
        hasher.update(source.as_bytes());
        hasher.update(b"\n");
        hasher.update(canonical_manifest_bytes(manifest).as_bytes());
        hasher.update(b"\n");
        hasher.update(format!("{runner_version}").as_bytes());
        Self(*hasher.finalize().as_bytes())
    }

    pub fn as_hex(&self) -> String {
        let mut s = String::with_capacity(64);
        for b in self.0 {
            s.push_str(&format!("{b:02x}"));
        }
        s
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CachedArtifact {
    pub key: ArtifactKey,
    pub library_path: PathBuf,
    pub runner_version: RunnerVersion,
    pub source_size_bytes: u64,
}

/// On-disk content-addressed artifact cache.
#[derive(Clone, Debug)]
pub struct ArtifactCache {
    root: PathBuf,
}

impl ArtifactCache {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn artifact_dir(&self, key: ArtifactKey) -> PathBuf {
        self.root.join(key.as_hex())
    }

    pub fn metadata_path(&self, key: ArtifactKey) -> PathBuf {
        self.artifact_dir(key).join("artifact.json")
    }

    pub fn lookup(&self, key: ArtifactKey) -> Option<CachedArtifact> {
        let meta_path = self.metadata_path(key);
        let bytes = std::fs::read(&meta_path).ok()?;
        let cached: CachedArtifact = serde_json::from_slice(&bytes).ok()?;
        if cached.key != key {
            return None;
        }
        if !cached.library_path.exists() {
            return None;
        }
        Some(cached)
    }

    pub fn store(
        &self,
        key: ArtifactKey,
        library_path: PathBuf,
        runner_version: RunnerVersion,
        source_size_bytes: u64,
    ) -> Result<CachedArtifact, BuildError> {
        let dir = self.artifact_dir(key);
        std::fs::create_dir_all(&dir)?;
        let artifact = CachedArtifact {
            key,
            library_path,
            runner_version,
            source_size_bytes,
        };
        let bytes = serde_json::to_vec_pretty(&artifact).map_err(|e| {
            BuildError::ArtifactCache(format!("metadata serialization failed: {e}"))
        })?;
        std::fs::write(self.metadata_path(key), bytes)?;
        Ok(artifact)
    }
}

fn canonical_manifest_bytes(m: &StrategyManifest) -> String {
    // Deterministic: sort dependency lists by name, drop ordering noise.
    let mut deps: Vec<_> = m.dependencies.iter().map(|d| (&d.name, &d.req)).collect();
    deps.sort_by_key(|d| d.0.as_str());
    let mut dev: Vec<_> = m
        .dev_dependencies
        .iter()
        .map(|d| (&d.name, &d.req))
        .collect();
    dev.sort_by_key(|d| d.0.as_str());
    let mut build: Vec<_> = m
        .build_dependencies
        .iter()
        .map(|d| (&d.name, &d.req))
        .collect();
    build.sort_by_key(|d| d.0.as_str());
    let mut s = String::new();
    s.push_str(&m.name);
    s.push('\n');
    s.push_str(&m.version);
    s.push('\n');
    for (n, r) in deps {
        s.push_str(&format!("dep:{n}={r}\n"));
    }
    for (n, r) in dev {
        s.push_str(&format!("dev:{n}={r}\n"));
    }
    for (n, r) in build {
        s.push_str(&format!("build:{n}={r}\n"));
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::driver::ManifestDep;
    use engine_rt::RUNNER_VERSION;

    fn manifest_a() -> StrategyManifest {
        StrategyManifest {
            name: "vxx_range".into(),
            version: "0.1.0".into(),
            dependencies: vec![
                ManifestDep {
                    name: "engine-rt".into(),
                    req: "*".into(),
                },
                ManifestDep {
                    name: "chrono".into(),
                    req: "*".into(),
                },
            ],
            dev_dependencies: vec![],
            build_dependencies: vec![],
        }
    }

    #[test]
    fn key_is_stable_across_dependency_order() {
        let mut m1 = manifest_a();
        m1.dependencies.reverse();
        let m2 = manifest_a();
        let k1 = ArtifactKey::from_inputs("source", &m1, RUNNER_VERSION);
        let k2 = ArtifactKey::from_inputs("source", &m2, RUNNER_VERSION);
        assert_eq!(k1, k2);
    }

    #[test]
    fn key_changes_with_source() {
        let m = manifest_a();
        let k1 = ArtifactKey::from_inputs("source v1", &m, RUNNER_VERSION);
        let k2 = ArtifactKey::from_inputs("source v2", &m, RUNNER_VERSION);
        assert_ne!(k1, k2);
    }

    #[test]
    fn key_changes_with_runner_version() {
        let m = manifest_a();
        let k1 = ArtifactKey::from_inputs("source", &m, RUNNER_VERSION);
        let k2 = ArtifactKey::from_inputs("source", &m, engine_rt::RunnerVersion::new(99, 0, 0));
        assert_ne!(k1, k2);
    }

    #[test]
    fn store_and_lookup_round_trip() {
        let tmp = tempdir();
        let cache = ArtifactCache::new(&tmp);
        let m = manifest_a();
        let key = ArtifactKey::from_inputs("source", &m, RUNNER_VERSION);

        // Create a fake library file in the temp dir so library_path resolves.
        let lib_path = tmp.join("libfake.so");
        std::fs::write(&lib_path, b"fake").unwrap();

        let stored = cache
            .store(key, lib_path.clone(), RUNNER_VERSION, 42)
            .unwrap();
        assert_eq!(stored.key, key);

        let looked_up = cache.lookup(key).expect("lookup should hit");
        assert_eq!(looked_up.key, key);
        assert_eq!(looked_up.library_path, lib_path);
        assert_eq!(looked_up.source_size_bytes, 42);
    }

    #[test]
    fn lookup_miss_when_library_file_missing() {
        let tmp = tempdir();
        let cache = ArtifactCache::new(&tmp);
        let m = manifest_a();
        let key = ArtifactKey::from_inputs("source", &m, RUNNER_VERSION);
        let lib_path = tmp.join("libgone.so");
        std::fs::write(&lib_path, b"x").unwrap();
        cache
            .store(key, lib_path.clone(), RUNNER_VERSION, 1)
            .unwrap();
        std::fs::remove_file(&lib_path).unwrap();
        assert!(cache.lookup(key).is_none());
    }

    fn tempdir() -> PathBuf {
        let dir = std::env::temp_dir().join(format!("strategy-gpt-cache-{}", uuid_hex()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn uuid_hex() -> String {
        // Avoid pulling uuid into build-pipeline just for tests; use blake3
        // of a unique-ish input.
        let pid = std::process::id();
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let hasher = blake3::hash(format!("{pid}-{now}").as_bytes());
        hasher.to_hex().to_string()[..16].to_string()
    }
}
