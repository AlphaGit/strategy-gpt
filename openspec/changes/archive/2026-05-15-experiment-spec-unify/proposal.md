# Unify experiment-spec; deprecate batch.json

## Why

The current engine input is `batch.json` plus a fan-out of CLI flags (`--artifact`, `--bars`, `--dataset-manifest`, `--worker`, `--time-cap-secs`, `--mem-cap-bytes`). The caller is responsible for keeping these aligned. There is no single artifact that captures *what experiment was run*, which blocks two near-term needs:

1. The upcoming parameter optimizer must reference a complete, replay-ready experiment definition for each candidate, not reconstruct one from scattered flags.
2. The ledger's reproducibility-from-ledger-alone promise becomes painful when the inputs are partitioned across the filesystem and the invocation shell.

This change introduces `experiment-spec.yaml`, a single declarative file that fully describes a backtest experiment: which artifact, which bars (already cached or auto-fetch via the gateway), which engine config, which run list. Later changes — `optimization-spec` and `optimize-command` — extend the same file with search and fold blocks.

## What Changes

- **NEW capability** `experiment-spec`: schema, validator, pydantic loader, JSON Schema export.
- Move fields from `batch.json` and CLI flags into the spec file: `artifact`, `bars`, `engine`, `params`, `runs` (modes, seed, slice), `parallelism`, `caps`.
- `bars` is polymorphic: `{dataset: <manifest_hash>}` (cache-resident) or `{request: BarRequest}` (auto-fetch via the gateway).
- `parallelism` accepts an integer or the literal `auto` (resolved by the runner to `max(1, os_cpu_count - 1)`).
- Drop `slippage_bps` from the `engine` block; per-run slippage continues to live on stress modes, owned by a future change.
- **BREAKING**: `strategy-gpt run --spec batch.json` no longer accepted. The legacy schema is rejected with a migration error pointing at the new format.
- Reference example migrated: `examples/vxx/batch.json` → `examples/vxx/experiment.yaml`.

## Capabilities

### New Capabilities

- `experiment-spec`: single-file declarative experiment definition used as the input to `strategy-gpt run` and (later) `strategy-gpt optimize`.

### Modified Capabilities

None in this change. The engine PyO3 binding still receives the same inner `BatchSpec`; the change is in how callers compose it.

## Impact

- **Code**: New module `python/strategy_gpt/experiment_spec.py` (pydantic models). `python/strategy_gpt/cli.py::run` rewritten to take `--spec experiment.yaml` only.
- **Examples**: `examples/vxx/batch.json` deleted; `examples/vxx/experiment.yaml` added.
- **Docs**: `docs/cli-cookbook.md` updated; CLAUDE.md domain vocabulary updated.
- **Dependencies**: `PyYAML` (already pulled in transitively); no Rust changes.
- **Out of scope (this change)**: search block, fold block, optimizer CLI, engine batch-packing behavior. Those land in subsequent changes that depend on this one.
