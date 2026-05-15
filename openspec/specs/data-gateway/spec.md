# Spec: data-gateway

## Purpose

Multi-provider market-data fetching with a content-addressed local cache, canonical normalization (UTC, calendar, adjustments), and multi-source consolidation. Consolidation policy is internal; divergence warnings are surfaced to the experiment ledger. Manifests pin the exact byte content used by a backtest for reproducibility.

## Requirements

### Requirement: Multi-provider fetch via pluggable provider trait

The Data Gateway SHALL support fetching OHLCV data from multiple providers via a pluggable provider trait. At v1 the supported providers are `yfinance` and a generic CSV/parquet adapter. The provider trait MUST expose a uniform `fetch(symbol, range, resolution)` operation returning normalized bars.

#### Scenario: Fetch from a registered provider

- **WHEN** the orchestrator requests `(symbol="VXX", range=2020..2024, resolution="1d")` and the `yfinance` provider is registered
- **THEN** the Data Gateway returns a normalized bar stream sourced from `yfinance`

#### Scenario: Adding a new provider

- **WHEN** a new provider is added implementing the provider trait
- **THEN** the Data Gateway accepts requests routed to that provider without any changes outside the provider crate

### Requirement: Content-addressed local cache, segmented by calendar year

The Data Gateway SHALL persist every successful fetch as a parquet blob keyed by `hash(provider, symbol, resolution, year, adjustment_policy, version)`. Cache lookups MUST be served from disk without contacting external providers when a matching key exists. Cache segmentation MUST be at calendar-year granularity.

#### Scenario: Cache hit on repeated fetch

- **WHEN** the same `(provider, symbol, resolution, year, adjustment_policy)` is requested twice
- **THEN** the second request reads from the local cache and makes no external network call

#### Scenario: Year segmentation enables partial reuse

- **WHEN** a request spans years 2020 through 2023 and only 2020 is cached
- **THEN** the Data Gateway fetches 2021–2023 from the provider, stores each year as its own cache blob, and returns the merged result

### Requirement: Normalization to canonical UTC, calendar, and adjustment policy

The Data Gateway SHALL normalize all provider data to UTC timestamps and to a per-instrument session calendar before caching. Adjustment policy (back-adjusted vs raw) MUST be tagged in the cache key so different policies do not collide. Exchange-local timestamps MAY be retained as a separate field for display.

#### Scenario: Provider returns exchange-local timestamps

- **WHEN** a provider returns timestamps in exchange-local time (e.g., America/New_York)
- **THEN** the Data Gateway converts them to UTC before storing and returns UTC bars to callers

### Requirement: Multi-source consolidation with internal-only policy and divergence warnings

When multiple providers cover the same `(symbol, resolution, range)`, the Data Gateway SHALL consolidate them into a single bar stream using policies configured **internally** to the consolidator (not exposed as per-request parameters). The consolidator SHALL emit divergence warnings for close mismatches above tolerance, volume mismatches, missing bars on a subset of providers, and timezone/calendar mismatches. Warnings MUST be persisted to the experiment ledger.

#### Scenario: Two providers disagree on close price beyond tolerance

- **WHEN** two providers report `close` values for the same `(symbol, ts)` differing by more than the consolidator's internal tolerance
- **THEN** the consolidator selects a value according to its internal precedence policy and writes a divergence warning to the ledger with both values

#### Scenario: One provider missing a bar present in others

- **WHEN** one provider has no bar for a `(symbol, ts)` but at least one other does
- **THEN** the consolidator returns the bar from the available provider and writes an info-level warning

#### Scenario: Consolidation policy is not configurable per request

- **WHEN** a caller attempts to override consolidation precedence or tolerance per request
- **THEN** the API rejects the override; consolidation policies are configured internally to the gateway only

### Requirement: Manifest pinning for reproducibility

Every dataset returned to a caller SHALL be accompanied by a manifest enumerating the cache blob hashes used to assemble it. The manifest MUST be sufficient to reproduce the exact byte-identical dataset from cache alone.

#### Scenario: Reproducing a backtest from a manifest

- **WHEN** a backtest run's manifest is replayed against the cache
- **THEN** the returned dataset is byte-identical to the original

### Requirement: Cache modes

The Data Gateway SHALL support `prefer-cache` (default), `validate` (cross-check cache against fresh fetch periodically), `force-refresh` (bypass cache for this call), and `offline` (fail on cache miss) modes selectable per request.

#### Scenario: Offline mode with cache miss

- **WHEN** a request is made in `offline` mode and no cache entry exists
- **THEN** the Data Gateway returns a structured error and makes no external call
