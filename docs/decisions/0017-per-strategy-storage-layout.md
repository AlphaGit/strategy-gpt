# 0017 — Per-strategy storage layout under `ledger/strategies/<strategy_name>/`

## Context

The hypothesis loop accumulates evidence per strategy: every candidate it
proposes (accepted or rejected) carries source blobs, LLM response blobs,
mini-optimize verdicts, and a citation set. The original ledger schema stored
hypothesis and decision records in a flat SQLite table keyed by hypothesis
id, with strategy identity implicit in the `target_metric` payload.

That worked when the loop was a stub: one strategy, few records, no source
blobs. As the loop grows into a real logic-search engine, three pressures
appear:

- Audit workloads are per-strategy: "show me everything we tried on
  `vxx_volatility_range`" is the dominant query. A flat table forces every
  read to scan strategy-by-name.
- Candidate source blobs are large (whole Rust file trees) and content
  addressable — they want a content-hash directory layout, not a column.
- LLM response blobs are per-decision and only meaningful in context with
  that decision; they want to live next to it on disk, not in a separate
  blob store.

## Decision

Each strategy gets a dedicated folder under `ledger/strategies/<strategy_name>/`
containing:

```
ledger/strategies/<strategy_name>/
  hypothesis_records.parquet     # all candidates emitted for this strategy
  decision_records.parquet       # accept/reject decisions paired by hypothesis id
  baseline/
    files_manifest.json          # paths + content hashes for baseline source
    best.json                    # baseline-best result cached for compare
  sources/
    <files_set_hash>/{Cargo.toml, src/lib.rs, params_schema.json, ...}
  responses/
    <decision_id>/
      stage1_idea.md
      stage2_commitments.md
      stage3_files.md
      repair_<n>.md              # one per repair attempt
```

Strategy identity is the strategy crate `name` (already stable repo-wide). The
flat native `runs.db` continues to record run-level facts (artifacts, dataset
manifest hash, parameters, modes); the per-strategy folder layers on top for
the hypothesis-loop-specific artifacts.

Cross-strategy queries iterate the directory listing — explicitly accepting
the O(N strategies) cost because the audit workload is dominated by single-
strategy reads.

## Consequences

- Audit is `ls ledger/strategies/<name>/` plus a parquet scan over two small
  files — trivial in DuckDB, polars, or any tabular tool.
- Source blobs deduplicate naturally via content hash: candidates that share
  baseline file content (the common case) share blobs.
- Replay is byte-identical: read the `files_manifest` blob set into a temp
  dir, hand it to the build pipeline, re-run mini-optimize with the seed
  recorded on the decision.
- Cross-strategy queries pay an O(N) directory iteration. Acceptable; the
  use case is rare and N is small (low tens, eventually).
- One-time migration cost from the flat ledger is bounded — the loop has no
  production consumers today, so the migration is essentially "discard the
  empty record set" and write fresh into the new layout.

## Alternatives Considered

- **Flat ledger with `strategy` column.** Rejected — every per-strategy
  query becomes a full-table scan with a `WHERE strategy = ?`. The native
  SQLite ledger could index that column, but the source/response blob
  problem remains: blobs want a directory layout, not table rows.
- **Per-decision content-addressed blob store (no per-strategy folder).**
  Rejected — operators routinely need to read several decisions for one
  strategy together (e.g. "what did the loop try last week on VXX?"). A
  flat blob CAS forces another index to recover that grouping.
- **Bundled per-strategy SQLite database.** Rejected — adds operational
  friction (a SQLite file per strategy) without solving the source-blob
  layout. Parquet + directory CAS is more transparent to standard tooling.

## Status

accepted
