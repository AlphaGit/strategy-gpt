## 1. Mkdocs plumbing

- [x] 1.1 Add `pymdownx.snippets` to `markdown_extensions` in `mkdocs.yml`, with `base_path: ["docs/_includes/"]` (and `docs/` if other snippets are foreseen).
- [x] 1.2 Create `docs/_includes/cli-cross-cutting.md` with the three reminders (progress modes, env vars, roots).
- [x] 1.3 Confirm `mkdocs build --strict` still passes with the new extension and the new include file present (referenced from nothing yet).

## 2. Walkthrough page scaffold

- [x] 2.1 Create `docs/guided-cli-walkthrough.md` with the page intro and the nine H2 stage headings, each with an explicit stable anchor (`{#stage-0-setup}` … `{#stage-8-reproduce}`).
- [x] 2.2 Embed `--8<-- "cli-cross-cutting.md"` at the foot of each stage (and confirm rendering once content is added).

## 3. Stage 0 — Setup

- [x] 3.1 Write the framing paragraph for one-time setup (operator-targeted, not contributor).
- [x] 3.2 Add the `strategy-gpt version` snippet + explanation.
- [x] 3.3 Add the `cargo build -p engine-worker` and `cargo build -p vxx-strategy` snippets with explanation of why each is required.
- [x] 3.4 Add the `maturin develop -m crates/py-bindings/Cargo.toml` snippet with explanation that it is required only after Rust binding changes.
- [x] 3.5 Add the `export ANTHROPIC_API_KEY` / `export OPENAI_API_KEY` snippet and which commands need which keys.
- [x] 3.6 Add the `export RUSTC_WRAPPER=sccache` snippet with the rebuild-speed rationale.
- [x] 3.7 Add the one-sentence disclaimer that `make lint`, `make test`, `pre-commit`, and `cargo check --workspace` are contributor / CI tooling and live in the contributor docs; link to `CONTRIBUTING.md` / `CLAUDE.md`.
- [x] 3.8 Add the "See also" subsection (link to `CLAUDE.md` build section).

## 4. Stage 1 — Explore

- [x] 4.1 Write the framing paragraph for surface discovery.
- [x] 4.2 Add the `strategy-gpt --help` and `strategy-gpt <cmd> --help` snippets.
- [x] 4.3 Add the `strategy-gpt hypothesize <name> --baseline-defaults --dry-run` snippet with explanation that it prints resolved deps without LLM/engine calls.
- [x] 4.4 Add the `strategy-gpt optimize --spec ... --benchmark --sample 3 --yes` snippet with the cost-prediction explanation.
- [x] 4.5 Add the "See also" subsection linking to `reference/hypothesize-cli.md` and any optimize benchmark reference.

## 5. Stage 2 — Acquire data

- [x] 5.1 Write the framing paragraph: what a dataset is (one-sentence primer), manifest hash, year segmentation.
- [x] 5.2 Add the `strategy-gpt fetch --provider yfinance` snippet with sample JSON output and the `manifest_hash` callout.
- [x] 5.3 Add the `--provider my_csv --csv-provider-dir ...` snippet plus expected CSV header.
- [x] 5.4 Cover the four cache modes (`prefer_cache`, `validate`, `force_refresh`, `offline`) — one short paragraph naming when to use each, with one `--mode offline` snippet for CI.
- [x] 5.5 Add the `strategy-gpt cache-stats --root cache` snippet.
- [x] 5.6 Port the Python "materialize bars to JSON" snippet from the old cookbook verbatim, with a sentence framing it as the one place where the operator must drop to Python because no CLI primitive exists.
- [x] 5.7 Add the "See also" subsection linking to `reference/experiment-spec.md`.

## 6. Stage 3 — Author

- [x] 6.1 Write the framing paragraph: `author` as the LLM-driven creation primitive, decisions.jsonl as authoritative state.
- [x] 6.2 Add the seed-and-no-seed invocation snippets.
- [x] 6.3 Add the edit-mode snippet (re-running against an existing name) with explanation of collision detection.
- [x] 6.4 Add the `--verify=batch` snippet with explanation.
- [x] 6.5 Add the `--k-repair-emit` and `--k-repair-build` snippets with explanation of the budget semantics.
- [x] 6.6 Cover `--model`, `--quiet`, `--verbose` in one paragraph with one snippet each.
- [x] 6.7 Cover the paste-aware multi-line input (`<<<` / `>>>`) with explanation.
- [x] 6.8 Cover the repair-exhaustion menu's four options in a short numbered list (not a table).
- [x] 6.9 Add at least one troubleshooting recipe (e.g. `smoke_failed: no_trades` or `exhausted repair budget` with the suggested operator action).
- [x] 6.10 Add the "See also" subsection linking to `tutorials/author-a-strategy.md`, `how-to/author-a-strategy.md`, `explanation/hand-authoring-a-strategy.md`.

## 7. Stage 4 — One-shot backtest

- [x] 7.1 Write the framing paragraph: when to use `run` (parameter tweak, single deterministic backtest).
- [x] 7.2 Add the `strategy-gpt run --spec ... --wait` snippet with the `JobStatus` shape callout (kept short; link out for the full schema).
- [x] 7.3 Add the async submit (drop `--wait`) snippet with explanation that polling currently lives in Python.
- [x] 7.4 Add the "tweak `runs[].params` and re-run" recipe with a short YAML diff snippet.
- [x] 7.5 Add the multi-run sweep snippet (add `runs[]` entries) and a one-sentence note about `parallelism`.
- [x] 7.6 Add the "See also" subsection linking to `reference/experiment-spec.md` and `reference/batch-spec.md`.

## 8. Stage 5 — Optimize

- [x] 8.1 Write the framing paragraph: per-fold search, OOS aggregation, ledgered runs.
- [x] 8.2 Add the default-method snippet (`strategy-gpt optimize --spec ...`).
- [x] 8.3 Cover the method overrides — one short paragraph per method (`sobol`, `recursive_grid`, `lhs_polish`, `successive_halving`, `cma_es`, `differential_evolution`) with the relevant snippet showing how to set it via CLI flag.
- [x] 8.4 Add the `--benchmark --sample N --yes` cost-prediction snippet.
- [x] 8.5 Add the `--parallelism auto|N` note.
- [x] 8.6 Add `optimize inspect <opt_id>` and `optimize inspect <opt_id> --trial <id>` snippets.
- [x] 8.7 Add `optimize replay <opt_id> --trial <id> --out result.json` snippet with the byte-identity callout.
- [x] 8.8 Add `optimize reselect <opt_id> --pbo-threshold 0.7` and `--robust-objective` snippets with one-sentence explanation of post-hoc selection.
- [x] 8.9 Add `optimize compare <opt_id> best.json best_<ts>.json` snippet.
- [x] 8.10 Add `--force` (publish despite PBO rejection) snippet with explanation that it records the override.
- [x] 8.11 Add the "See also" subsection linking to `reference/objective-spec.md`, `explanation/overfitting-and-selection.md`, `how-to/interpret-pbo-rejection.md`.

## 9. Stage 6 — Hypothesize + KB

- [x] 9.1 Write the framing paragraph: workflow nodes, baseline modes, persisted decisions.
- [x] 9.2 Add the `--baseline-defaults` snippet with the "cheapest path" framing.
- [x] 9.3 Add the `--baseline-from <opt-run-id>` snippet with the "rigorous path" framing.
- [x] 9.4 Add the `--dry-run` snippet for inspecting resolved deps.
- [x] 9.5 Add the `--quick` snippet with the iteration-speed framing.
- [x] 9.6 Cover `--objective` in one snippet.
- [x] 9.7 Cover the per-stage model flags (`--model-stage1` … `--model-stage3`, `--model-critique`, `--model-rank`) in one paragraph with one example snippet.
- [x] 9.8 Cover the KB store flags (`--kb-store`, `--rebuild-kb`) with a one-sentence note that the store lazy-builds on first run.
- [x] 9.9 Add `strategy-gpt recent-decisions` snippet with explanation of accepted vs rejected vs deferred outcomes.
- [x] 9.10 Add `strategy-gpt hypothesis replay <decision-id>` snippet with a brief description of the JSON summary it prints and the `--strategy` scope flag.
- [x] 9.11 Add `strategy-gpt hypothesis diff <decision-id>` snippet with explanation of the unified diff against the baseline source bundle.
- [x] 9.12 Add the "See also" subsection linking to `how-to/run-hypothesize.md` and `how-to/read-progress-output.md`.

## 10. Stage 7 — Iterate

- [x] 10.1 Write the framing paragraph: one improvement cycle, then the next.
- [x] 10.2 Add the numbered list "re-optimize after acceptance → next `hypothesize --baseline-from <new opt-id>`" with one snippet per step.
- [x] 10.3 Add the `recent-decisions --limit 50` snippet for cross-iteration audit.
- [x] 10.4 Add the "See also" subsection linking back to Stage 5 and Stage 6.

## 11. Stage 8 — Reproduce / debug

- [x] 11.1 Write the framing paragraph: byte-identity guarantee, what is pinned.
- [x] 11.2 Add the `strategy-gpt replay --run-id <ledger-run-id>` snippet with explanation that upstream providers are not contacted.
- [x] 11.3 Add the cross-reference back to Stage 5's `optimize replay` snippet.
- [x] 11.4 Add the `--mode offline` recipe for CI reproducibility.
- [x] 11.5 Add one footnote-style sentence pointing at `ledger/optimizations.sqlite` for the curious (without specifying schema).
- [x] 11.6 Add the "See also" subsection linking to `explanation/domain-vocabulary.md`.

## 12. Cookbook removal + inbound link migration

- [x] 12.1 Delete `docs/how-to/cli-cookbook.md`.
- [x] 12.2 Repoint `docs/for-quants/index.md:12` to the walkthrough (or the most relevant stage anchor).
- [x] 12.3 Repoint `docs/how-to/index.md:5` to the walkthrough.
- [x] 12.4 Repoint `docs/tutorials/author-a-strategy.md:181` to `guided-cli-walkthrough.md#stage-3-author`.
- [x] 12.5 Repoint `docs/tutorials/first-backtest.md:163` to `guided-cli-walkthrough.md#stage-4-one-shot` (or the chosen stage slug).
- [x] 12.6 Repoint `docs/tutorials/running-an-optimization.md:235` to `guided-cli-walkthrough.md#stage-5-optimize`.
- [x] 12.7 Run `grep -rln 'cli-cookbook' docs/` and confirm no matches remain.

## 13. Nav and index updates

- [x] 13.1 Update `mkdocs.yml` nav: add the walkthrough as a top-level entry positioned before the Tutorials group; remove the cookbook entry from the How-to group.
- [x] 13.2 Update `docs/index.md` to surface the walkthrough as the recommended starting point for operators.
- [x] 13.3 Update `docs/how-to/index.md` to remove the cookbook line (already repointed in 12.3 but the standalone entry must also go).

## 14. Validation

- [x] 14.1 Run `make lint` end-to-end; confirm `mkdocs build --strict` exits zero with no broken-link warnings.
- [x] 14.2 Run `mkdocs serve` locally; visually confirm the walkthrough renders, the cross-cutting include resolves identically in every stage, and every stage anchor is reachable from the rendered ToC.
- [x] 14.3 Verify the four `--8<--` references resolve to the canonical include (no copy-paste divergence).
- [x] 14.4 Confirm `grep -rln 'cli-cookbook'` returns no matches under `docs/`.

## 15. Spec archival

- [x] 15.1 After implementation, run `openspec validate guided-cli-walkthrough`.
- [x] 15.2 Open the implementation PR with the proposal, design, spec delta, and tasks linked from the description.
