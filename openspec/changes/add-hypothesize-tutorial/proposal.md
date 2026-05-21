## Why

Tutorials currently cover backtest, strategy authoring, and parameter optimization — the hypothesis-loop surface (`strategy-gpt hypothesize`, `strategy-gpt hypothesis replay`, `strategy-gpt hypothesis diff`) has reference and how-to pages but no learn-by-doing walkthrough. New operators have nowhere in the Diátaxis "tutorials" quadrant to land on the loop's CLI before reaching for the reference or the Python entry. A fourth tutorial closes that gap.

## What Changes

- Add `docs/tutorials/hypothesize-loop.md` walking the reader through the hypothesize CLI surface end-to-end, conforming to the five-section tutorial skeleton (`Learning goal`, `Prerequisites`, `Walkthrough`, `What you just did`, `What next`).
- The walkthrough exercises three commands in order:
  - `strategy-gpt hypothesize <strategy> --dry-run` — input validation against the bundled VXX crate, no LLM call required.
  - `strategy-gpt hypothesis replay <decision_id>` — reconstruct a recorded candidate from a fixture per-strategy ledger shipped under `kb/fixtures/` (or generated on demand from `python -m strategy_gpt.smoke`).
  - `strategy-gpt hypothesis diff <decision_id>` — render the candidate-vs-baseline source diff.
- Link the new page from `docs/tutorials/index.md` and add it to the `mkdocs.yml` Tutorials nav.
- Promote the four-page tutorial set in `openspec/specs/documentation/spec.md`: the "initial tutorials" requirement gains `hypothesize-loop.md` as a fourth required page.

## Capabilities

### New Capabilities

(none — this is a documentation expansion)

### Modified Capabilities

- `documentation`: the "Repository ships at least three initial tutorials" requirement becomes "at least four", and the named set grows to include `hypothesize-loop.md`.

## Impact

- `docs/tutorials/hypothesize-loop.md` (new)
- `docs/tutorials/index.md` (one new bullet)
- `mkdocs.yml` (one new nav entry under Tutorials)
- `openspec/specs/documentation/spec.md` (requirement text + scenario list updated via spec delta)
- Optional new fixture under `kb/fixtures/` or a short generator step using `python -m strategy_gpt.smoke` so the `replay`/`diff` walkthrough is reproducible without LLM API keys.
- No code changes to the CLI itself — the tutorial documents commands that already exist in `python/strategy_gpt/cli.py`.
