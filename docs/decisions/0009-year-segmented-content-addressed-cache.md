# 0009 — Year-segmented content-addressed data cache

## Context

The data gateway fetches OHLCV bars from multiple providers, normalizes them to UTC, optionally consolidates across providers, and exposes them to the engine. Re-fetching is expensive (rate limits, provider quirks) and unnecessary — historical bars almost never change. We need cache lookups to be reproducible across machines, replayable from a manifest hash alone, and trivially diffable when the same window is fetched from different providers.

## Decision

Bars are cached as **year-segmented content-addressed parquet blobs** under `cache/<provider>/<symbol>/<year>.parquet`. The "content address" is a hash of the normalized bar content; manifest entries in the ledger record the hash so a replay can verify the cache file is byte-identical to what produced the original `BacktestResult`.

## Consequences

- Replays from the ledger reconstruct datasets without re-fetching; the cache plus the ledger is sufficient.
- Year segmentation bounds the size of any single parquet read and keeps per-year diffs cheap when a provider revises history.
- The cache is human-inspectable: `cache/yfinance/VXX/2018.parquet` is a real file.
- Cache mode (`prefer_cache`, `validate`, `force_refresh`, `offline`) is a CLI knob; `validate` mode refetches and diffs against the cache, surfacing provider drift to the ledger.
- A provider that revises bars retroactively causes a hash mismatch; the gateway records this as a divergence rather than silently overwriting.

## Alternatives Considered

- **Single-file cache per dataset.** Loses the per-year locality and forces a full re-read on any modification.
- **HTTP-style cache with ETag.** Doesn't translate to provider APIs and doesn't guarantee byte-identical replays across machines.
- **No cache.** Replays would require re-fetch; rate-limit and provider-deprecation risk grows unbounded.

## Status

accepted
