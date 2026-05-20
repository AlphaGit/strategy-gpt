# Architecture Decision Records

ADRs capture the *why* behind load-bearing technical decisions. Each ADR follows the template at [`0000-adr-template.md`](0000-adr-template.md).

## Index

| # | Title | Status |
|---|---|---|
| [0001](0001-rust-execution-layer.md) | Rust for the execution layer | accepted |
| [0002](0002-python-orchestration.md) | Python for orchestration | accepted |
| [0003](0003-pyo3-trusted-crate-boundary.md) | PyO3 in-process for trusted crates only | accepted |
| [0004](0004-engine-worker-subprocess-arrow-ipc.md) | Engine workers via subprocess + Arrow IPC | accepted |
| [0005](0005-worker-process-isolation-no-sandbox.md) | Worker process isolation, no sandboxing | accepted |
| [0006](0006-sealed-strategy-trait.md) | Sealed `Strategy` trait, no backwards compatibility | accepted |
| [0007](0007-sqlite-parquet-ledger.md) | SQLite + parquet sidecars for the ledger | accepted |
| [0008](0008-hybrid-graph-vector-kb.md) | Hybrid graph + vector knowledge base over SQLite | accepted |
| [0009](0009-year-segmented-content-addressed-cache.md) | Year-segmented content-addressed data cache | accepted |
| [0010](0010-abort-on-failure-batch-semantics.md) | Abort-on-failure batch semantics | accepted |
| [0011](0011-pbo-threshold-default-0_5.md) | PBO threshold default 0.5 | accepted |
| [0012](0012-oos-aggregator-mean-only.md) | Mean as the only OOS aggregator | accepted |
| [0013](0013-rust-toolchain-pin-1-82.md) | Rust 1.82.0 toolchain pin | accepted |
| [0014](0014-lint-stance.md) | Lint stance: Rust tool defaults + Python strict | accepted |
| [0015](0015-docs-platform-mkdocs-mike-pages.md) | Docs platform: MkDocs Material + mike + GitHub Pages | accepted |
| [0016](0016-prompt-api-md-authoritative-llm-context.md) | `engine-rt/PROMPT_API.md` is the authoritative LLM context | accepted |
| [0017](0017-per-strategy-storage-layout.md) | Per-strategy storage layout under `ledger/strategies/<strategy_name>/` | accepted |
| [0018](0018-no-versioning-on-hypothesis-records.md) | No `runner_version` field on hypothesis records | accepted |
| [0019](0019-multi-stage-llm-emission.md) | Multi-stage LLM emission for hypothesis candidates | accepted |
| [0020](0020-comparative-falsification-variance-aware-epsilon.md) | Comparative falsification with a variance-aware acceptance floor | accepted |

## Conventions

- File names: `^[0-9]{4}-[a-z0-9-]+\.md$`
- Sections (in order): Context, Decision, Consequences, Alternatives Considered, Status.
- Superseded ADRs remain in the tree with `Status: superseded by NNNN`.
