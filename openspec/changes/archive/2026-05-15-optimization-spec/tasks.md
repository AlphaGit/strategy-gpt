## 1. Schema extension

- [x] 1.1 Extend the experiment-spec JSON Schema with optional `optimize` and `folds` blocks.
- [x] 1.2 `optimize` fields: `method`, `seed`, `aggregator`, `space` (per-param shapes), per-method knobs sub-blocks, `persist` (root + name).
- [x] 1.3 `folds` fields: `count`, `scheme` (`rolling` | `anchored`), `gap`, optional `warmup_bars`.
- [x] 1.4 Add example experiment-spec files: one with a search block, one without (matches change 1's example).

## 2. Pydantic models

- [x] 2.1 Add `OptimizeBlock`, `SearchSpace`, `ParamSpace` (`FloatParam | IntParam | ChoiceParam`), `FoldsBlock`, and method-specific sub-models in `python/strategy_gpt/experiment_spec.py`.
- [x] 2.2 Validate that `optimize.space` keys are disjoint from `runs[0].params` (fixed params).
- [x] 2.3 Validate `folds.count >= 2`, `folds.gap >= 0`, and `folds.warmup_bars >= 0` if present.

## 3. Rename `walk_forward` → `folds` in objectives

- [x] 3.1 Update `crates/objectives` Rust struct field name and serde rename; add a custom deserializer that rejects the legacy `walk_forward` key with a structured migration error.
- [x] 3.2 Update `python/strategy_gpt/objectives.py` pydantic model mirror.
- [x] 3.3 Update `crates/vxx-strategy/objective.yaml` to use the new block name.
- [x] 3.4 Update `python/strategy_gpt/types.py` and any other surface that mentions `walk_forward`.

## 4. Fold derivation utility

- [x] 4.1 Add a pure-Python helper `derive_folds(base_slice, folds_block) -> list[FoldRange]` returning `(train, oos)` tuples.
- [x] 4.2 `rolling`: equal-width sliding window; `anchored`: train start fixed, train end grows, OOS slides.
- [x] 4.3 Honor `gap` (skip N bars between train end and OOS start) and `warmup_bars` (subtract from train start of fold 0).
- [x] 4.4 Unit tests across `count ∈ {2, 4, 8}`, both schemes, `gap ∈ {0, 1, 5}`, and one warmup case.

## 5. Validator integration

- [x] 5.1 Spec validator rejects `optimize` block without `folds` block (folds are required for any search).
- [x] 5.2 Spec validator rejects search-space params absent from the strategy's metadata (cross-check against the strategy artifact's declared params).

## 6. Docs

- [x] 6.1 Update `docs/experiment-spec.md` with the `optimize` and `folds` blocks, an example for each search method's knobs, and the fold derivation rules.
- [x] 6.2 Update `CLAUDE.md` domain vocabulary: drop "walk-forward" mentions; introduce "fold scheme" and "OOS aggregate".
