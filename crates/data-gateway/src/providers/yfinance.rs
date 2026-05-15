//! Yahoo Finance provider (public chart endpoint).
//!
//! Hits `https://query2.finance.yahoo.com/v8/finance/chart/{SYMBOL}` and maps
//! the response into `engine_rt::Bar`. Gated behind the `yfinance` cargo
//! feature so CSV-only builds avoid the HTTP/TLS dependency surface.
//!
//! Daily/weekly resolution is the supported path. Intraday windows are bounded
//! by Yahoo's retention (1m ≈ last 7d, 5m ≈ last 60d) — the provider will
//! return whatever Yahoo serves for the requested year and surface no bars
//! when the window predates retention.
//!
//! No API key, no auth. Yahoo can throttle or block; treat failures as
//! provider errors (the gateway will not cache empty responses).

use chrono::{TimeZone, Utc};
use engine_rt::{Bar, Resolution};
use serde::Deserialize;

use crate::bar::AdjustmentPolicy;
use crate::error::DataGatewayError;
use crate::provider::{Provider, ProviderQuery};

const DEFAULT_BASE_URL: &str = "https://query2.finance.yahoo.com/v8/finance/chart";
const DEFAULT_TIMEOUT_SECS: u64 = 30;
const USER_AGENT: &str = "Mozilla/5.0 (strategy-gpt yfinance provider)";

pub struct YfinanceProvider {
    name: String,
    base_url: String,
    timeout_secs: u64,
}

impl YfinanceProvider {
    pub fn new(name: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            base_url: DEFAULT_BASE_URL.to_string(),
            timeout_secs: DEFAULT_TIMEOUT_SECS,
        }
    }

    /// Override the upstream base URL. Used by tests to point at a mock server;
    /// production callers should rely on the default.
    pub fn with_base_url(mut self, base_url: impl Into<String>) -> Self {
        self.base_url = base_url.into();
        self
    }

    pub fn with_timeout_secs(mut self, secs: u64) -> Self {
        self.timeout_secs = secs;
        self
    }

    fn build_url(&self, query: &ProviderQuery) -> Result<String, DataGatewayError> {
        let interval =
            interval_for(query.resolution).ok_or_else(|| DataGatewayError::Provider {
                provider: self.name.clone(),
                message: format!(
                    "resolution {:?} not supported by yfinance",
                    query.resolution
                ),
            })?;
        let period1 = query.year_start().timestamp();
        let period2 = query.year_end().timestamp();
        Ok(format!(
            "{base}/{symbol}?period1={p1}&period2={p2}&interval={interval}&events=history&includeAdjustedClose=true",
            base = self.base_url.trim_end_matches('/'),
            symbol = query.symbol,
            p1 = period1,
            p2 = period2,
            interval = interval,
        ))
    }
}

impl Provider for YfinanceProvider {
    fn name(&self) -> &str {
        &self.name
    }

    fn fetch_year(&self, query: &ProviderQuery) -> Result<Vec<Bar>, DataGatewayError> {
        let url = self.build_url(query)?;
        let agent = ureq::AgentBuilder::new()
            .timeout(std::time::Duration::from_secs(self.timeout_secs))
            .user_agent(USER_AGENT)
            .build();
        let response = agent
            .get(&url)
            .call()
            .map_err(|e| DataGatewayError::Provider {
                provider: self.name.clone(),
                message: format!("http: {e}"),
            })?;
        if response.status() != 200 {
            return Err(DataGatewayError::Provider {
                provider: self.name.clone(),
                message: format!("http status {}", response.status()),
            });
        }
        let payload: ChartResponse =
            response
                .into_json()
                .map_err(|e| DataGatewayError::Provider {
                    provider: self.name.clone(),
                    message: format!("decode: {e}"),
                })?;
        parse_payload(&self.name, query, &payload)
    }
}

fn interval_for(resolution: Resolution) -> Option<&'static str> {
    match resolution {
        Resolution::Minute => Some("1m"),
        Resolution::FiveMinute => Some("5m"),
        Resolution::FifteenMinute => Some("15m"),
        Resolution::Hour => Some("60m"),
        Resolution::Day => Some("1d"),
        Resolution::Week => Some("1wk"),
    }
}

fn parse_payload(
    provider: &str,
    query: &ProviderQuery,
    payload: &ChartResponse,
) -> Result<Vec<Bar>, DataGatewayError> {
    if let Some(err) = &payload.chart.error {
        return Err(DataGatewayError::Provider {
            provider: provider.into(),
            message: format!("yahoo error {}: {}", err.code, err.description),
        });
    }
    let Some(result) = payload.chart.result.as_ref().and_then(|r| r.first()) else {
        return Ok(Vec::new());
    };
    let timestamps = match &result.timestamp {
        Some(ts) => ts,
        None => return Ok(Vec::new()),
    };
    let quote = result
        .indicators
        .quote
        .first()
        .ok_or_else(|| DataGatewayError::Provider {
            provider: provider.into(),
            message: "missing quote block".into(),
        })?;

    let len = timestamps.len();
    let check = |name: &str, v: &Vec<Option<f64>>| {
        if v.len() != len {
            Err(DataGatewayError::Provider {
                provider: provider.into(),
                message: format!("{name} length {} != timestamps {}", v.len(), len),
            })
        } else {
            Ok(())
        }
    };
    check("open", &quote.open)?;
    check("high", &quote.high)?;
    check("low", &quote.low)?;
    check("close", &quote.close)?;
    check("volume", &quote.volume)?;

    let adjclose: Option<&Vec<Option<f64>>> = match query.adjustment {
        AdjustmentPolicy::BackAdjusted => result
            .indicators
            .adjclose
            .as_ref()
            .and_then(|v| v.first())
            .map(|a| &a.adjclose),
        AdjustmentPolicy::Raw => None,
    };
    if let Some(v) = adjclose {
        check("adjclose", v)?;
    }

    let year_start = query.year_start();
    let year_end = query.year_end();
    let mut bars = Vec::with_capacity(len);
    for i in 0..len {
        let ts = Utc
            .timestamp_opt(timestamps[i], 0)
            .single()
            .ok_or_else(|| DataGatewayError::Provider {
                provider: provider.into(),
                message: format!("invalid timestamp {}", timestamps[i]),
            })?;
        if ts < year_start || ts >= year_end {
            continue;
        }
        let (open, high, low, raw_close, volume) = match (
            quote.open[i],
            quote.high[i],
            quote.low[i],
            quote.close[i],
            quote.volume[i],
        ) {
            (Some(o), Some(h), Some(l), Some(c), Some(v)) => (o, h, l, c, v),
            _ => continue,
        };
        let close = match (query.adjustment, adjclose, raw_close) {
            (AdjustmentPolicy::BackAdjusted, Some(v), _) => v[i].unwrap_or(raw_close),
            _ => raw_close,
        };
        bars.push(Bar {
            symbol: query.symbol.clone(),
            ts,
            resolution: query.resolution,
            open,
            high,
            low,
            close,
            volume,
        });
    }
    bars.sort_by_key(|b| b.ts);
    Ok(bars)
}

#[derive(Debug, Deserialize)]
struct ChartResponse {
    chart: Chart,
}

#[derive(Debug, Deserialize)]
struct Chart {
    #[serde(default)]
    result: Option<Vec<ChartResult>>,
    #[serde(default)]
    error: Option<ChartError>,
}

#[derive(Debug, Deserialize)]
struct ChartError {
    code: String,
    description: String,
}

#[derive(Debug, Deserialize)]
struct ChartResult {
    #[serde(default)]
    timestamp: Option<Vec<i64>>,
    indicators: Indicators,
}

#[derive(Debug, Deserialize)]
struct Indicators {
    quote: Vec<QuoteBlock>,
    #[serde(default)]
    adjclose: Option<Vec<AdjCloseBlock>>,
}

#[derive(Debug, Deserialize)]
struct QuoteBlock {
    open: Vec<Option<f64>>,
    high: Vec<Option<f64>>,
    low: Vec<Option<f64>>,
    close: Vec<Option<f64>>,
    volume: Vec<Option<f64>>,
}

#[derive(Debug, Deserialize)]
struct AdjCloseBlock {
    adjclose: Vec<Option<f64>>,
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Datelike;

    fn sample_payload() -> ChartResponse {
        let ts_2024 = Utc
            .with_ymd_and_hms(2024, 1, 2, 14, 30, 0)
            .unwrap()
            .timestamp();
        let ts_2024_b = Utc
            .with_ymd_and_hms(2024, 1, 3, 14, 30, 0)
            .unwrap()
            .timestamp();
        ChartResponse {
            chart: Chart {
                result: Some(vec![ChartResult {
                    timestamp: Some(vec![ts_2024, ts_2024_b]),
                    indicators: Indicators {
                        quote: vec![QuoteBlock {
                            open: vec![Some(100.0), Some(101.0)],
                            high: vec![Some(102.0), Some(103.0)],
                            low: vec![Some(99.0), Some(100.5)],
                            close: vec![Some(101.5), Some(102.5)],
                            volume: vec![Some(1_000.0), Some(1_500.0)],
                        }],
                        adjclose: Some(vec![AdjCloseBlock {
                            adjclose: vec![Some(101.0), Some(102.0)],
                        }]),
                    },
                }]),
                error: None,
            },
        }
    }

    #[test]
    fn parse_payload_back_adjusted_uses_adjclose() {
        let query = ProviderQuery {
            symbol: "VXX".into(),
            year: 2024,
            resolution: Resolution::Day,
            adjustment: AdjustmentPolicy::BackAdjusted,
        };
        let bars = parse_payload("yfinance", &query, &sample_payload()).unwrap();
        assert_eq!(bars.len(), 2);
        assert_eq!(bars[0].close, 101.0);
        assert_eq!(bars[1].close, 102.0);
        assert_eq!(bars[0].ts.year(), 2024);
    }

    #[test]
    fn parse_payload_raw_uses_close() {
        let query = ProviderQuery {
            symbol: "VXX".into(),
            year: 2024,
            resolution: Resolution::Day,
            adjustment: AdjustmentPolicy::Raw,
        };
        let bars = parse_payload("yfinance", &query, &sample_payload()).unwrap();
        assert_eq!(bars[0].close, 101.5);
        assert_eq!(bars[1].close, 102.5);
    }

    #[test]
    fn parse_payload_clips_to_requested_year() {
        let mut payload = sample_payload();
        let off_year = Utc
            .with_ymd_and_hms(2023, 12, 31, 23, 0, 0)
            .unwrap()
            .timestamp();
        payload.chart.result.as_mut().unwrap()[0]
            .timestamp
            .as_mut()
            .unwrap()[0] = off_year;
        let query = ProviderQuery {
            symbol: "VXX".into(),
            year: 2024,
            resolution: Resolution::Day,
            adjustment: AdjustmentPolicy::BackAdjusted,
        };
        let bars = parse_payload("yfinance", &query, &payload).unwrap();
        assert_eq!(bars.len(), 1);
        assert_eq!(bars[0].ts.year(), 2024);
    }

    #[test]
    fn parse_payload_skips_null_rows() {
        let mut payload = sample_payload();
        payload.chart.result.as_mut().unwrap()[0].indicators.quote[0].close[0] = None;
        let query = ProviderQuery {
            symbol: "VXX".into(),
            year: 2024,
            resolution: Resolution::Day,
            adjustment: AdjustmentPolicy::Raw,
        };
        let bars = parse_payload("yfinance", &query, &payload).unwrap();
        assert_eq!(bars.len(), 1);
    }

    #[test]
    fn parse_payload_surfaces_yahoo_error() {
        let payload = ChartResponse {
            chart: Chart {
                result: None,
                error: Some(ChartError {
                    code: "Not Found".into(),
                    description: "No data found, symbol may be delisted".into(),
                }),
            },
        };
        let query = ProviderQuery {
            symbol: "BOGUS".into(),
            year: 2024,
            resolution: Resolution::Day,
            adjustment: AdjustmentPolicy::Raw,
        };
        let err = parse_payload("yfinance", &query, &payload).unwrap_err();
        assert!(err.to_string().contains("Not Found"));
    }

    #[test]
    fn parse_payload_empty_when_no_result() {
        let payload = ChartResponse {
            chart: Chart {
                result: Some(vec![]),
                error: None,
            },
        };
        let query = ProviderQuery {
            symbol: "VXX".into(),
            year: 2024,
            resolution: Resolution::Day,
            adjustment: AdjustmentPolicy::Raw,
        };
        let bars = parse_payload("yfinance", &query, &payload).unwrap();
        assert!(bars.is_empty());
    }

    #[test]
    fn build_url_encodes_periods_and_interval() {
        let provider = YfinanceProvider::new("yfinance").with_base_url("https://example/v8/chart");
        let query = ProviderQuery {
            symbol: "VXX".into(),
            year: 2024,
            resolution: Resolution::Day,
            adjustment: AdjustmentPolicy::BackAdjusted,
        };
        let url = provider.build_url(&query).unwrap();
        assert!(url.contains("/VXX?"));
        assert!(url.contains("interval=1d"));
        assert!(url.contains("includeAdjustedClose=true"));
        let p1 = Utc
            .with_ymd_and_hms(2024, 1, 1, 0, 0, 0)
            .unwrap()
            .timestamp();
        let p2 = Utc
            .with_ymd_and_hms(2025, 1, 1, 0, 0, 0)
            .unwrap()
            .timestamp();
        assert!(url.contains(&format!("period1={p1}")));
        assert!(url.contains(&format!("period2={p2}")));
    }

    /// Live HTTP test. Ignored by default — opt in via:
    ///   `cargo test -p data-gateway --features yfinance -- --ignored yfinance_live`
    #[test]
    #[ignore = "hits live Yahoo Finance; requires network"]
    fn yfinance_live_fetches_vxx_2023() {
        let provider = YfinanceProvider::new("yfinance");
        let query = ProviderQuery {
            symbol: "VXX".into(),
            year: 2023,
            resolution: Resolution::Day,
            adjustment: AdjustmentPolicy::BackAdjusted,
        };
        let bars = provider.fetch_year(&query).expect("live fetch");
        assert!(
            bars.len() > 100,
            "VXX 2023 daily bars should be >100, got {}",
            bars.len()
        );
        for bar in &bars {
            assert_eq!(bar.symbol, "VXX");
            assert!(bar.high >= bar.low);
        }
    }
}
