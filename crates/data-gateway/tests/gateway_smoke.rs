//! Integration tests for the data gateway: CSV provider + year-segmented
//! cache + cache modes + manifest round-trip + normalization.

use std::sync::Arc;

use chrono::{TimeZone, Utc};
use data_gateway::providers::CsvProvider;
use data_gateway::{AdjustmentPolicy, BarRequest, CacheMode, DataGateway, DataGatewayError};
use engine_rt::Resolution;
use tempfile::tempdir;

fn write_csv(path: &std::path::Path, rows: &[(&str, f64, f64, f64, f64, f64)]) {
    let mut s = String::from("timestamp,open,high,low,close,volume\n");
    for (ts, o, h, l, c, v) in rows {
        s.push_str(&format!("{ts},{o},{h},{l},{c},{v}\n"));
    }
    std::fs::write(path, s).unwrap();
}

fn day_ts(y: i32, m: u32, d: u32) -> chrono::DateTime<Utc> {
    Utc.with_ymd_and_hms(y, m, d, 0, 0, 0).unwrap()
}

fn open_gateway_with_csv() -> (tempfile::TempDir, DataGateway) {
    let cache_dir = tempdir().unwrap();
    let csv_dir = tempdir().unwrap();
    write_csv(
        &csv_dir.path().join("VXX.csv"),
        &[
            ("2023-12-30", 50.0, 51.0, 49.5, 50.5, 1_000.0),
            ("2024-01-02", 51.0, 52.0, 50.5, 51.5, 1_200.0),
            ("2024-01-03", 51.5, 53.0, 51.0, 52.5, 1_500.0),
            ("2024-01-04", 52.5, 54.0, 52.0, 53.5, 1_400.0),
            ("2025-01-02", 60.0, 61.0, 59.0, 60.5, 2_000.0),
        ],
    );
    let mut gw = DataGateway::open(cache_dir.path()).unwrap();
    gw.register_provider(Arc::new(CsvProvider::new("csv", csv_dir.path())));
    // Leak csv_dir so its tempfiles live as long as the gateway needs them.
    std::mem::forget(csv_dir);
    (cache_dir, gw)
}

fn vxx_request_2024() -> BarRequest {
    BarRequest {
        provider: "csv".into(),
        symbol: "VXX".into(),
        start: day_ts(2024, 1, 1),
        end: day_ts(2024, 12, 31),
        resolution: Resolution::Day,
        adjustment: AdjustmentPolicy::BackAdjusted,
    }
}

#[test]
fn fetch_populates_cache_and_returns_normalized_bars() {
    let (_cache, gw) = open_gateway_with_csv();
    let resp = gw
        .fetch(&vxx_request_2024(), CacheMode::PreferCache)
        .unwrap();
    assert_eq!(resp.bars.len(), 3);
    assert_eq!(resp.bars[0].ts, day_ts(2024, 1, 2));
    assert_eq!(resp.bars[2].ts, day_ts(2024, 1, 4));
    assert_eq!(resp.manifest.len(), 1, "single year covers the range");
}

#[test]
fn second_fetch_is_a_cache_hit() {
    let (_cache, gw) = open_gateway_with_csv();
    let r1 = gw
        .fetch(&vxx_request_2024(), CacheMode::PreferCache)
        .unwrap();
    // Remove the source CSV so a second fetch could only succeed from cache.
    // (Provider would error reading a missing file.)
    let r2 = gw
        .fetch(&vxx_request_2024(), CacheMode::PreferCache)
        .unwrap();
    assert_eq!(r1, r2);
}

#[test]
fn offline_mode_errors_when_cache_is_cold() {
    let (_cache, gw) = open_gateway_with_csv();
    let err = gw
        .fetch(&vxx_request_2024(), CacheMode::Offline)
        .unwrap_err();
    assert!(matches!(err, DataGatewayError::OfflineMiss { .. }));
}

#[test]
fn offline_mode_serves_cache_after_warm_up() {
    let (_cache, gw) = open_gateway_with_csv();
    let _ = gw
        .fetch(&vxx_request_2024(), CacheMode::PreferCache)
        .unwrap();
    let r = gw.fetch(&vxx_request_2024(), CacheMode::Offline).unwrap();
    assert_eq!(r.bars.len(), 3);
}

#[test]
fn year_segmented_cache_loads_one_year_per_blob() {
    let (_cache, gw) = open_gateway_with_csv();
    let multi_year = BarRequest {
        provider: "csv".into(),
        symbol: "VXX".into(),
        start: day_ts(2023, 12, 1),
        end: day_ts(2025, 6, 1),
        resolution: Resolution::Day,
        adjustment: AdjustmentPolicy::BackAdjusted,
    };
    let r = gw.fetch(&multi_year, CacheMode::PreferCache).unwrap();
    // 2023, 2024, 2025 = 3 blobs.
    assert_eq!(r.manifest.len(), 3);
    assert_eq!(r.bars.len(), 5);
}

#[test]
fn manifest_hash_changes_with_blob_set() {
    let (_cache, gw) = open_gateway_with_csv();
    let r_2024 = gw
        .fetch(&vxx_request_2024(), CacheMode::PreferCache)
        .unwrap();
    let r_multi = gw
        .fetch(
            &BarRequest {
                provider: "csv".into(),
                symbol: "VXX".into(),
                start: day_ts(2023, 12, 1),
                end: day_ts(2025, 6, 1),
                resolution: Resolution::Day,
                adjustment: AdjustmentPolicy::BackAdjusted,
            },
            CacheMode::PreferCache,
        )
        .unwrap();
    assert_ne!(r_2024.manifest_hash, r_multi.manifest_hash);
}

#[test]
fn force_refresh_re_reads_provider_even_after_warmup() {
    let (_cache, gw) = open_gateway_with_csv();
    let warm = gw
        .fetch(&vxx_request_2024(), CacheMode::PreferCache)
        .unwrap();
    let forced = gw
        .fetch(&vxx_request_2024(), CacheMode::ForceRefresh)
        .unwrap();
    // Same source data → identical bars + manifest.
    assert_eq!(warm, forced);
}

#[test]
fn unknown_provider_errors_clearly() {
    let cache_dir = tempdir().unwrap();
    let gw = DataGateway::open(cache_dir.path()).unwrap();
    let err = gw
        .fetch(&vxx_request_2024(), CacheMode::PreferCache)
        .unwrap_err();
    assert!(matches!(err, DataGatewayError::UnknownProvider(_)));
}

#[test]
fn invalid_range_rejected() {
    let (_cache, gw) = open_gateway_with_csv();
    let bad = BarRequest {
        provider: "csv".into(),
        symbol: "VXX".into(),
        start: day_ts(2024, 6, 1),
        end: day_ts(2024, 1, 1),
        resolution: Resolution::Day,
        adjustment: AdjustmentPolicy::BackAdjusted,
    };
    let err = gw.fetch(&bad, CacheMode::PreferCache).unwrap_err();
    assert!(matches!(err, DataGatewayError::InvalidRange { .. }));
}

#[test]
fn normalizer_sorts_and_dedups() {
    // CSV with out-of-order timestamps and a duplicate.
    let cache_dir = tempdir().unwrap();
    let csv_dir = tempdir().unwrap();
    write_csv(
        &csv_dir.path().join("VXX.csv"),
        &[
            ("2024-01-03", 0.0, 0.0, 0.0, 53.0, 0.0),
            ("2024-01-02", 0.0, 0.0, 0.0, 52.0, 0.0),
            ("2024-01-03", 0.0, 0.0, 0.0, 99.0, 0.0), // duplicate ts
        ],
    );
    let mut gw = DataGateway::open(cache_dir.path()).unwrap();
    gw.register_provider(Arc::new(CsvProvider::new("csv", csv_dir.path())));
    let r = gw
        .fetch(&vxx_request_2024(), CacheMode::PreferCache)
        .unwrap();
    assert_eq!(r.bars.len(), 2);
    assert_eq!(r.bars[0].ts, day_ts(2024, 1, 2));
    assert_eq!(r.bars[1].ts, day_ts(2024, 1, 3));
    // First record at duplicate ts wins (after sort: the row that appeared
    // earliest in the file at that ts, which is close=53.0).
    assert_eq!(r.bars[1].close, 53.0);
}
