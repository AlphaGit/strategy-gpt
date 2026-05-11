use thiserror::Error;

#[derive(Debug, Error)]
pub enum DataGatewayError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),

    #[error("sqlite error: {0}")]
    Sqlite(#[from] rusqlite::Error),

    #[error("json error: {0}")]
    Json(#[from] serde_json::Error),

    #[error("provider not registered: {0}")]
    UnknownProvider(String),

    #[error("provider `{provider}` returned an error: {message}")]
    Provider { provider: String, message: String },

    #[error("cache miss in offline mode: provider={provider} symbol={symbol} year={year}")]
    OfflineMiss {
        provider: String,
        symbol: String,
        year: i32,
    },

    #[error("requested range is invalid: start={start} >= end={end}")]
    InvalidRange { start: String, end: String },

    #[error("invalid bar: {0}")]
    InvalidBar(String),

    #[error("internal: {0}")]
    Internal(String),
}
