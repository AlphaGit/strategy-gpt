use thiserror::Error;

#[derive(Debug, Error)]
pub enum BuildError {
    #[error("source lint failed: {0}")]
    SourceLint(String),

    #[error("manifest lint failed: {0}")]
    ManifestLint(String),

    #[error("whitelist load failed: {0}")]
    Whitelist(String),

    #[error("io error: {0}")]
    Io(#[from] std::io::Error),

    #[error("cargo invocation failed: {0}")]
    Cargo(String),

    #[error("artifact cache error: {0}")]
    ArtifactCache(String),

    #[error("migration error: {0}")]
    Migration(String),

    #[error("params_schema.json error: {0}")]
    ParamsSchema(String),
}
