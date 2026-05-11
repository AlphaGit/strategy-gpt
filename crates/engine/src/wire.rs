//! Wire protocol shared between the engine coordinator (parent) and the
//! `engine-worker` binary (child).
//!
//! Framing: each message is an 8-byte little-endian `u64` length prefix
//! followed by exactly that many bytes of payload. Payload is compact JSON
//! (no whitespace). Stdin carries one [`WorkerRequest`]; stdout carries one
//! [`WorkerResponse`]. Stderr is reserved for human-readable diagnostics and
//! is never decoded.
//!
//! JSON-over-pipes is the v1 transport. The schema is shape-compatible with
//! Arrow IPC framing (single record batch per message), so the upgrade path
//! when columnar payloads become useful for the broader pipeline is a
//! mechanical swap.

use std::io::{Read, Write};

use engine_rt::Bar;
use serde::{Deserialize, Serialize};

use crate::result::BacktestResult;
use crate::spec::{EngineConfig, RunSpec};

/// One run dispatched to a worker subprocess.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct WorkerRequest {
    /// Path to the strategy `cdylib` artifact the worker should `libloading`-load.
    pub artifact_path: String,
    pub run: RunSpec,
    pub bars: Vec<Bar>,
    pub engine: EngineConfig,
    /// Opaque strategy artifact identifier that should appear in
    /// `BacktestResult.meta.strategy_artifact`. The coordinator passes the
    /// `BatchSpec.strategy.0` value here so result `meta` matches the batch.
    pub strategy_artifact: String,
    pub dataset_manifest: String,
}

/// One result returned by a worker subprocess.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum WorkerResponse {
    Ok { result: Box<BacktestResult> },
    Error { message: String },
}

/// Maximum payload size accepted by [`read_framed`]. Defensive bound against a
/// peer claiming an outsized frame; 256 MiB is well above any realistic
/// `BacktestResult` JSON.
pub const MAX_FRAME_BYTES: u64 = 256 * 1024 * 1024;

#[derive(Debug, thiserror::Error)]
pub enum WireError {
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
    #[error("frame too large: {bytes} > {max}")]
    FrameTooLarge { bytes: u64, max: u64 },
    #[error("unexpected eof while reading frame")]
    UnexpectedEof,
    #[error("json: {0}")]
    Json(#[from] serde_json::Error),
}

/// Write a length-prefixed JSON frame.
pub fn write_message<W: Write, T: Serialize>(out: &mut W, value: &T) -> Result<(), WireError> {
    let bytes = serde_json::to_vec(value)?;
    let len = bytes.len() as u64;
    out.write_all(&len.to_le_bytes())?;
    out.write_all(&bytes)?;
    out.flush()?;
    Ok(())
}

/// Read a length-prefixed JSON frame.
pub fn read_message<R: Read, T: for<'de> Deserialize<'de>>(input: &mut R) -> Result<T, WireError> {
    let mut len_buf = [0u8; 8];
    read_exact_or_eof(input, &mut len_buf)?;
    let len = u64::from_le_bytes(len_buf);
    if len > MAX_FRAME_BYTES {
        return Err(WireError::FrameTooLarge {
            bytes: len,
            max: MAX_FRAME_BYTES,
        });
    }
    let mut payload = vec![0u8; len as usize];
    read_exact_or_eof(input, &mut payload)?;
    let value = serde_json::from_slice(&payload)?;
    Ok(value)
}

fn read_exact_or_eof<R: Read>(input: &mut R, buf: &mut [u8]) -> Result<(), WireError> {
    match input.read_exact(buf) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => Err(WireError::UnexpectedEof),
        Err(e) => Err(WireError::Io(e)),
    }
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;

    use chrono::{TimeZone, Utc};
    use engine_rt::Resolution;

    use crate::spec::TimeRange;

    use super::*;

    fn sample_request() -> WorkerRequest {
        let ts = Utc.with_ymd_and_hms(2024, 1, 1, 0, 0, 0).unwrap();
        WorkerRequest {
            artifact_path: "/tmp/strategy.dylib".into(),
            run: RunSpec {
                params: serde_json::json!({}),
                modes: vec![],
                seed: 42,
                slice: TimeRange {
                    start: ts,
                    end: ts + chrono::Duration::days(1),
                },
            },
            bars: vec![Bar {
                symbol: "X".into(),
                ts,
                resolution: Resolution::Day,
                open: 1.0,
                high: 2.0,
                low: 0.5,
                close: 1.5,
                volume: 100.0,
            }],
            engine: EngineConfig::default(),
            strategy_artifact: "hash".into(),
            dataset_manifest: "manifest".into(),
        }
    }

    #[test]
    fn round_trip_request() {
        let req = sample_request();
        let mut buf = Vec::new();
        write_message(&mut buf, &req).expect("write");
        let mut cur = Cursor::new(buf);
        let decoded: WorkerRequest = read_message(&mut cur).expect("read");
        assert_eq!(decoded.artifact_path, req.artifact_path);
        assert_eq!(decoded.bars.len(), 1);
    }

    #[test]
    fn frame_too_large_is_rejected() {
        let mut bad = (MAX_FRAME_BYTES + 1).to_le_bytes().to_vec();
        bad.extend_from_slice(b"{}");
        let mut cur = Cursor::new(bad);
        let err = read_message::<_, WorkerRequest>(&mut cur).expect_err("should reject");
        matches!(err, WireError::FrameTooLarge { .. });
    }

    #[test]
    fn truncated_frame_is_unexpected_eof() {
        let mut buf = Vec::new();
        write_message(&mut buf, &sample_request()).expect("write");
        buf.truncate(10);
        let mut cur = Cursor::new(buf);
        let err = read_message::<_, WorkerRequest>(&mut cur).expect_err("should fail");
        matches!(err, WireError::UnexpectedEof);
    }
}
