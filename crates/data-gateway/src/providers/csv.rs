//! Generic CSV provider.
//!
//! Reads `<base_dir>/<symbol>.csv` with a header line `timestamp,open,high,low,close,volume`.
//! Timestamps are RFC3339 (with explicit timezone) or `YYYY-MM-DD` (interpreted
//! as UTC midnight). The provider yields bars whose `ts` falls inside the
//! requested year.

use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::PathBuf;

use chrono::{DateTime, NaiveDate, TimeZone, Utc};
use engine_rt::{Bar, Resolution};

use crate::error::DataGatewayError;
use crate::provider::{Provider, ProviderQuery};

pub struct CsvProvider {
    name: String,
    base_dir: PathBuf,
}

impl CsvProvider {
    /// `name` is the registration name; `base_dir` is where `<symbol>.csv`
    /// files live. Calling code controls both.
    pub fn new(name: impl Into<String>, base_dir: impl Into<PathBuf>) -> Self {
        Self {
            name: name.into(),
            base_dir: base_dir.into(),
        }
    }
}

impl Provider for CsvProvider {
    fn name(&self) -> &str {
        &self.name
    }

    fn fetch_year(&self, query: &ProviderQuery) -> Result<Vec<Bar>, DataGatewayError> {
        let path = self.base_dir.join(format!("{}.csv", query.symbol));
        if !path.exists() {
            return Err(DataGatewayError::Provider {
                provider: self.name.clone(),
                message: format!("file not found: {}", path.display()),
            });
        }
        let file = File::open(&path)?;
        let reader = BufReader::new(file);

        let mut bars = Vec::new();
        let year_start = query.year_start();
        let year_end = query.year_end();

        for (idx, line) in reader.lines().enumerate() {
            let line = line?;
            if idx == 0 {
                // header sanity: must start with `timestamp`
                if !line.to_ascii_lowercase().starts_with("timestamp") {
                    return Err(DataGatewayError::Provider {
                        provider: self.name.clone(),
                        message: format!(
                            "CSV at {} missing `timestamp,open,high,low,close,volume` header",
                            path.display()
                        ),
                    });
                }
                continue;
            }
            if line.trim().is_empty() {
                continue;
            }
            let bar = parse_row(&line, &query.symbol, query.resolution).map_err(|e| {
                DataGatewayError::Provider {
                    provider: self.name.clone(),
                    message: format!("line {idx} of {}: {e}", path.display()),
                }
            })?;
            if bar.ts >= year_start && bar.ts < year_end {
                bars.push(bar);
            }
        }

        bars.sort_by_key(|b| b.ts);
        Ok(bars)
    }
}

fn parse_row(line: &str, symbol: &str, resolution: Resolution) -> Result<Bar, String> {
    let parts: Vec<&str> = line.split(',').map(str::trim).collect();
    if parts.len() != 6 {
        return Err(format!(
            "expected 6 comma-separated fields, got {}",
            parts.len()
        ));
    }
    let ts = parse_timestamp(parts[0])?;
    let open: f64 = parts[1].parse().map_err(|e| format!("open: {e}"))?;
    let high: f64 = parts[2].parse().map_err(|e| format!("high: {e}"))?;
    let low: f64 = parts[3].parse().map_err(|e| format!("low: {e}"))?;
    let close: f64 = parts[4].parse().map_err(|e| format!("close: {e}"))?;
    let volume: f64 = parts[5].parse().map_err(|e| format!("volume: {e}"))?;
    Ok(Bar {
        symbol: symbol.into(),
        ts,
        resolution,
        open,
        high,
        low,
        close,
        volume,
    })
}

fn parse_timestamp(s: &str) -> Result<DateTime<Utc>, String> {
    // Try RFC3339 first (with timezone).
    if let Ok(t) = DateTime::parse_from_rfc3339(s) {
        return Ok(t.with_timezone(&Utc));
    }
    // Then a plain date.
    if let Ok(d) = NaiveDate::parse_from_str(s, "%Y-%m-%d") {
        return Ok(Utc
            .from_utc_datetime(&d.and_hms_opt(0, 0, 0).expect("valid midnight"))
            .with_timezone(&Utc));
    }
    Err(format!("timestamp `{s}` not RFC3339 nor YYYY-MM-DD"))
}
