# Optimization spec — search and fold blocks on experiment-spec

## Why

Once `experiment-spec.yaml` is the single source of truth for an experiment (per `2026-05-14-experiment-spec-unify`), the parameter optimizer needs two additional declarative blocks to describe *what to search* and *how to split data into folds*. Today there is nowhere in any spec to declare a search space, a search method's tuning knobs, the number of folds, or the fold scheme. The reference example hardcoded all of these in Python.

This change extends `experiment-spec` with optional `optimize` and `folds` blocks, and renames the existing "walk-forward" terminology in `objectives` to plain "fold" terminology so the system speaks one language. It does *not* introduce the `optimize` CLI command — that lands in `2026-05-14-optimize-command` and depends on this change.

## What Changes

- **MODIFIED capability** `experiment-spec`:
  - Add an optional `optimize` block describing the search method, the parameter search space, method-specific knobs, the seed, the aggregator, and the persistence target.
  - Add an optional `folds` block describing fold count, scheme (`rolling` or `anchored`), gap, and optional warmup bars. When absent, falls back to `objectives.walk_forward` (legacy compatibility) until that field is renamed and migrated.
  - When `optimize` is present, `runs` MAY contain a single template run whose `params` are overridden by the search; or `runs` MAY be omitted, in which case the engine config + slice + modes form the template.
- **MODIFIED capability** `objectives`:
  - Rename the "Walk-forward configuration" requirement to "Fold configuration"; rename `walk_forward` block in the objective YAML to `folds` (same fields).
  - Update the OOS-gate requirement to use "fold OOS aggregate" wording.
  - No semantic change beyond terminology.
- Drop the term "walk-forward" from all surfaced spec text; "folds" + "OOS" carry the meaning.

## Capabilities

### Modified Capabilities

- `experiment-spec`: add `optimize` and `folds` blocks.
- `objectives`: rename `walk_forward` → `folds`; drop "walk-forward" wording.

## Impact

- **Code**:
  - `python/strategy_gpt/experiment_spec.py` — add `OptimizeBlock`, `FoldsBlock` pydantic models.
  - `crates/objectives` (Rust) — rename serde field `walk_forward` → `folds`; bump struct version.
  - `python/strategy_gpt/objectives.py` — mirror the rename.
  - `crates/vxx-strategy/objective.yaml` — rename block.
- **Migration**: One reference objective file; rename by hand. No external users.
- **Compat**: The objectives loader rejects the legacy `walk_forward` key with a migration error pointing at `folds`.
- **Out of scope (this change)**: optimizer algorithm, CLI command, persistence layout, benchmark mode. All in the follow-up change.
