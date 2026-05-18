# 0007 — SQLite + parquet sidecars for the ledger

## Context

The experiment ledger records every run the engine produces, every accepted/rejected hypothesis, every divergence warning from the data gateway, and the metadata required to byte-identically replay any historical run from the ledger plus the local cache (see `openspec/specs/experiment-ledger/spec.md`). The data model has two natures: small relational rows (run metadata, decisions, divergences) and larger columnar payloads (per-bar trades, signals, equity curves, exec logs).

## Decision

The ledger is **append-only SQLite** for the relational layer, with **parquet sidecars** stored alongside the SQLite file for columnar payloads. Each run row in SQLite references parquet files by content-addressed path. The pair is the canonical persistence unit; the ledger directory is shippable as-is.

## Consequences

- Operational simplicity: a single directory backs the ledger, no server, no daemon.
- SQLite append-only + indexes give good ergonomics for the queries the decision-log bootstrap and the `recent-decisions` view need.
- Parquet sidecars give columnar reads (essential for `trials.parquet` selection-layer post-processing) without inflating the SQLite file.
- Backup / migration story: copy the directory. No streaming or schema-migration tooling required at this scale.
- A future need for cross-machine concurrent writers would force a server-backed store; that requirement does not exist today.

## Alternatives Considered

- **Postgres.** Operational overhead unjustified by single-machine research workloads.
- **DuckDB only.** Strong columnar story but weaker mutation ergonomics for the append-only relational layer; the SQLite + parquet split keeps each tool in its lane.
- **Flat JSON / JSONL.** No indexes, no columnar reads, no transactional guarantees. Rejected.

## Status

accepted
