//! Post-hoc regime annotation for [`BacktestResult::regimes`].
//!
//! Annotates the run timeline with two regime axes:
//! - **Volatility regime**: realized vol over a rolling window, terciled
//!   into `low_vol` / `med_vol` / `high_vol`.
//! - **Trend regime**: sign of a rolling SMA slope, classified as
//!   `uptrend` / `downtrend` / `chop`.
//!
//! Regimes are computed from the input bar stream, not the equity curve,
//! so they describe the market the strategy traded — not the strategy's
//! reaction to it.

use chrono::{DateTime, Utc};
use engine_rt::Bar;

use crate::result::RegimeTag;

const DEFAULT_VOL_WINDOW: usize = 20;
const DEFAULT_TREND_WINDOW: usize = 20;
/// Slope threshold (in price-units / bar) below which trend is considered
/// "chop". Computed as `slope.abs() < pct * mean_close * 1/window`. We pick a
/// percentage relative to price magnitude so the threshold is unitless.
const TREND_CHOP_PCT: f64 = 0.0005;

pub fn annotate_regimes(bars: &[Bar]) -> Vec<RegimeTag> {
    let mut tags = annotate_volatility(bars, DEFAULT_VOL_WINDOW);
    tags.extend(annotate_trend(bars, DEFAULT_TREND_WINDOW));
    tags
}

fn annotate_volatility(bars: &[Bar], window: usize) -> Vec<RegimeTag> {
    if bars.len() <= window + 1 {
        return Vec::new();
    }
    // Compute rolling stddev of log returns.
    let log_rets: Vec<f64> = bars
        .windows(2)
        .map(|w| {
            if w[0].close > 0.0 && w[1].close > 0.0 {
                (w[1].close / w[0].close).ln()
            } else {
                0.0
            }
        })
        .collect();
    let mut rolling: Vec<(DateTime<Utc>, f64)> = Vec::with_capacity(log_rets.len());
    for i in window..=log_rets.len() {
        let slice = &log_rets[i - window..i];
        let mean = slice.iter().sum::<f64>() / window as f64;
        let var = slice.iter().map(|r| (r - mean).powi(2)).sum::<f64>() / window as f64;
        let stdev = var.sqrt();
        // The bar this rolling window ends on is bars[i] (since log_rets[k]
        // corresponds to bars[k+1]).
        rolling.push((bars[i].ts, stdev));
    }
    if rolling.is_empty() {
        return Vec::new();
    }
    let (low, high) = terciles(rolling.iter().map(|(_, v)| *v));
    classify_runs(&rolling, |v| {
        if *v < low {
            "low_vol"
        } else if *v > high {
            "high_vol"
        } else {
            "med_vol"
        }
    })
}

fn annotate_trend(bars: &[Bar], window: usize) -> Vec<RegimeTag> {
    if bars.len() < window + 1 {
        return Vec::new();
    }
    let mut points: Vec<(DateTime<Utc>, f64, f64)> = Vec::with_capacity(bars.len());
    for i in window..bars.len() {
        let slice = &bars[i - window..=i];
        let n = slice.len() as f64;
        let mean_close = slice.iter().map(|b| b.close).sum::<f64>() / n;
        // Linear regression slope of close on bar index.
        let mean_x = (n - 1.0) / 2.0;
        let mut num = 0.0;
        let mut den = 0.0;
        for (j, b) in slice.iter().enumerate() {
            let x = j as f64;
            num += (x - mean_x) * (b.close - mean_close);
            den += (x - mean_x).powi(2);
        }
        let slope = if den > 0.0 { num / den } else { 0.0 };
        points.push((bars[i].ts, slope, mean_close));
    }
    let series: Vec<(DateTime<Utc>, (f64, f64))> =
        points.into_iter().map(|(ts, s, m)| (ts, (s, m))).collect();
    classify_runs(&series, |(slope, mean_close)| {
        let threshold = mean_close * TREND_CHOP_PCT;
        if *slope > threshold {
            "uptrend"
        } else if *slope < -threshold {
            "downtrend"
        } else {
            "chop"
        }
    })
}

fn terciles(it: impl Iterator<Item = f64>) -> (f64, f64) {
    let mut v: Vec<f64> = it.collect();
    v.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    if v.is_empty() {
        return (0.0, 0.0);
    }
    let n = v.len();
    let lo = v[n / 3];
    let hi = v[(2 * n / 3).min(n - 1)];
    (lo, hi)
}

fn classify_runs<T>(
    series: &[(DateTime<Utc>, T)],
    classify: impl Fn(&T) -> &'static str,
) -> Vec<RegimeTag>
where
    T: Copy,
{
    if series.is_empty() {
        return Vec::new();
    }
    let mut out = Vec::new();
    let mut run_start = series[0].0;
    let mut run_label = classify(&series[0].1);
    let mut last_ts = series[0].0;
    for (ts, v) in &series[1..] {
        let label = classify(v);
        if label != run_label {
            out.push(RegimeTag {
                start: run_start,
                end: *ts,
                label: run_label.into(),
            });
            run_start = *ts;
            run_label = label;
        }
        last_ts = *ts;
    }
    out.push(RegimeTag {
        start: run_start,
        end: last_ts,
        label: run_label.into(),
    });
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;
    use engine_rt::Resolution;

    fn day(i: usize) -> DateTime<Utc> {
        Utc.with_ymd_and_hms(2024, 1, 1, 0, 0, 0).unwrap() + chrono::Duration::days(i as i64)
    }

    fn bars_with_closes(closes: &[f64]) -> Vec<Bar> {
        closes
            .iter()
            .enumerate()
            .map(|(i, c)| Bar {
                symbol: "VXX".into(),
                ts: day(i),
                resolution: Resolution::Day,
                open: *c,
                high: *c,
                low: *c,
                close: *c,
                volume: 1.0,
            })
            .collect()
    }

    #[test]
    fn empty_or_short_input_emits_no_tags() {
        assert!(annotate_regimes(&[]).is_empty());
        assert!(annotate_regimes(&bars_with_closes(&[1.0, 2.0, 3.0])).is_empty());
    }

    #[test]
    fn rising_series_classified_as_uptrend() {
        let closes: Vec<f64> = (1..=40).map(|i| 100.0 + i as f64).collect();
        let bars = bars_with_closes(&closes);
        let tags = annotate_trend(&bars, DEFAULT_TREND_WINDOW);
        assert!(tags.iter().any(|t| t.label == "uptrend"));
    }

    #[test]
    fn flat_series_classified_as_chop() {
        let closes: Vec<f64> = vec![100.0; 40];
        let bars = bars_with_closes(&closes);
        let tags = annotate_trend(&bars, DEFAULT_TREND_WINDOW);
        assert!(tags.iter().all(|t| t.label == "chop"));
    }
}
