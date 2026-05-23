## 1. Fixture generator

- [x] 1.1 Inspect `python/strategy_gpt/smoke.py` and confirm whether `run_smoke()` already writes per-strategy ledger rows or only emits the in-memory `SmokeReport`.
- [x] 1.2 If not already supported, extend the smoke driver with a `--ledger-root <path>` option that, after `hypothesize()` returns, persists the recorded `HypothesisRecordV2` + `DecisionRecordV2` rows via `PerStrategyLedger.append_*` and writes the source bundle under `sources/<files_set_hash>/`.
- [x] 1.3 Decide and document the strategy-name slug the smoke driver records under (e.g. `vxx_volatility_range` or `smoke_strategy`). The tutorial uses this exact slug.
- [x] 1.4 Add a regression test in `python/tests/test_smoke.py` asserting that running the smoke driver with `--ledger-root` produces at least one decision row that `_find_decision_record` can resolve.

## 2. Tutorial page

- [x] 2.1 Create `docs/tutorials/hypothesize-loop.md` with the five required sections in order: `Learning goal`, `Prerequisites`, `Walkthrough`, `What you just did`, `What next`.
- [x] 2.2 Walkthrough step 1: `python -m strategy_gpt.smoke --ledger-root ledger` (or the option name finalized in 1.2). Show the expected stdout summary or "writes files (no stdout)" callout.
- [x] 2.3 Walkthrough step 2: `strategy-gpt hypothesize <strategy> --dry-run`. Show the resolved-flags JSON output.
- [x] 2.4 Walkthrough step 3: `ls ledger/strategies/<strategy>/` or an equivalent listing so the reader has a real `<decision_id>` to copy.
- [x] 2.5 Walkthrough step 4: `strategy-gpt hypothesis replay <decision_id>`. Show the summary JSON (strategy, decision_id, hypothesis_id, outcome, files_in_bundle, ...).
- [x] 2.6 Walkthrough step 5: `strategy-gpt hypothesis diff <decision_id>`. Show the unified-diff header line and a representative file diff.
- [x] 2.7 Write the `What you just did` paragraph naming the hypothesize CLI surface, the per-strategy ledger, and the source bundle store.
- [x] 2.8 Write the `What next` bullets linking to `docs/how-to/run-hypothesize.md`, `docs/reference/hypothesize-cli.md`, the hypothesis-loop spec, and the relevant explanation page (e.g. overfitting & selection if applicable, or the hypothesis-loop ADR if one exists).

## 3. Navigation wiring

- [x] 3.1 Add a bullet to `docs/tutorials/index.md` pointing at `hypothesize-loop.md` with a one-sentence summary.
- [x] 3.2 Add the page to `mkdocs.yml` under the Tutorials nav section in walkthrough order (after `running-an-optimization.md`).
- [x] 3.3 Run `make docs-serve` and click through every link on the new page; verify `mkdocs build --strict` is clean (this is the gate `make lint` runs).

## 4. Spec sync and verification

- [x] 4.1 Run `make lint` to confirm `mkdocs build --strict` passes with the new page.
- [x] 4.2 Run `openspec validate add-hypothesize-tutorial --strict` and resolve any spec-format issues.
- [x] 4.3 Manually walk the tutorial from a clean checkout (or scratch worktree) without setting `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` to verify the no-keys scenario in the spec.
- [x] 4.4 Open the PR; once merged, run `openspec archive add-hypothesize-tutorial` so the `documentation` spec absorbs the MODIFIED + ADDED requirements.
