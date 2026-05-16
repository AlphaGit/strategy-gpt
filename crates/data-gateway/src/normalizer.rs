//! Bar normalization: UTC enforcement, range clipping, sort/dedup.
//!
//! Exchange-calendar alignment is a planned follow-up.

use chrono::{DateTime, Utc};
use engine_rt::Bar;

use crate::error::DataGatewayError;

pub fn normalize_bars(
    mut bars: Vec<Bar>,
    start: DateTime<Utc>,
    end: DateTime<Utc>,
) -> Result<Vec<Bar>, DataGatewayError> {
    if start >= end {
        return Err(DataGatewayError::InvalidRange {
            start: start.to_rfc3339(),
            end: end.to_rfc3339(),
        });
    }
    for b in &bars {
        if !b.open.is_finite() || !b.close.is_finite() {
            return Err(DataGatewayError::InvalidBar(format!(
                "non-finite OHLC at {}",
                b.ts
            )));
        }
        if b.high < b.low {
            return Err(DataGatewayError::InvalidBar(format!(
                "high<low at {}: {}<{}",
                b.ts, b.high, b.low
            )));
        }
    }
    bars.sort_by_key(|b| b.ts);
    // Dedup keeping the first record at each timestamp.
    bars.dedup_by_key(|b| b.ts);
    bars.retain(|b| b.ts >= start && b.ts < end);
    Ok(bars)
}
