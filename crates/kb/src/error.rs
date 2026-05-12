use thiserror::Error;

#[derive(Debug, Error)]
pub enum KbError {
    #[error("sqlite error: {0}")]
    Sqlite(#[from] rusqlite::Error),

    #[error("io error: {0}")]
    Io(#[from] std::io::Error),

    #[error("json error: {0}")]
    Json(#[from] serde_json::Error),

    #[error("toml deserialization error: {0}")]
    Toml(#[from] toml::de::Error),

    #[error("unknown node kind: {0}")]
    UnknownNodeKind(String),

    #[error("unknown edge kind: {0}")]
    UnknownEdgeKind(String),

    #[error("source not found: {0}")]
    SourceNotFound(String),

    #[error("node not found: {0}")]
    NodeNotFound(String),

    #[error("embedding dimension mismatch: expected {expected}, got {got}")]
    EmbeddingDim { expected: usize, got: usize },

    #[error("invalid configuration: {0}")]
    Config(String),

    #[error("ingestion failed: {0}")]
    Ingestion(String),
}
