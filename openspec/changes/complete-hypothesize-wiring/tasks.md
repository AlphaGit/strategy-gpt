## 1. Construction helpers module

- [x] 1.1 Create `python/strategy_gpt/hypothesize_wiring.py` with module docstring and public API surface
- [x] 1.2 Implement `resolve_crate_paths(strategy_name, crates_dir) -> CratePaths` returning the absolute paths to `Cargo.toml`, `src/lib.rs`, `intent.toml`, `smoke.toml`, optional `experiment.yaml`; raise a typed error for missing required files
- [x] 1.3 Implement `build_kb_client(store_path, sources_path, rebuild=False) -> KbClient` — auto-ingests `sources_path` into `store_path` if missing; honors `rebuild=True`
- [x] 1.4 Add a `KbClient.from_store_path(path)` constructor on `kb_query.KbClient` if one doesn't already exist
- [x] 1.5 Implement `build_stage_client(model_overrides: dict[StageName, str]) -> StageReasoningClient` — wraps `reasoning_clients._StageRouter` with default models per stage; applies overrides
- [x] 1.6 Implement `build_evaluate_fold(crate_paths, engine_worker_path, gateway_root) -> tuple[EvaluateFoldFn, str]` returning (callable, dataset_manifest_hash); supports both `experiment.yaml` fold-scheme dispatch and `smoke.toml` single-fold fallback
- [x] 1.7 Implement `load_baseline_from_optimize(opt_run_id, ledger_root) -> BaselineTuple` reading `best.json` + per-fold artifacts; raise a typed error for missing run id
- [x] 1.8 Implement `compute_baseline_defaults(crate_paths, evaluate_fold, fold_count) -> BaselineTuple` invoking the evaluator at default params for every fold; carry the param defaults from `intent.toml.param_schema_sketch`
- [x] 1.9 Define `BaselineTuple` dataclass holding `result`, `files`, `params_schema`, `per_fold_scores`, `metrics`, `aggregate_score`, `source`
- [x] 1.10 Implement `resolve_kept_bounds(intent_toml_data) -> dict` from the param_schema_sketch (min/max per param)
- [x] 1.11 Implement `resolve_objective_metric(intent_toml_data, override) -> str` with a fallback constant (`sharpe_ratio`)

## 2. Reasoning-client and KB plumbing

- [x] 2.1 Verify `reasoning_clients._StageRouter` covers all stages the workflow dispatches; widen if missing
- [x] 2.2 Add a `verify_api_keys()` helper that raises a clear error when neither `ANTHROPIC_API_KEY` nor `OPENAI_API_KEY` is set
- [x] 2.3 Verify the KB ingestion path against `kb/sources.toml` runs cleanly from a fresh repo checkout; document any prerequisites in the wiring module's docstring
- [x] 2.4 Add a one-time progress banner emitted by `build_kb_client` on the first-build path (no banner on reuse)

## 3. Baseline loader

- [x] 3.1 Identify the canonical `best.json` schema produced by `optimization_runner.run_optimization`; add a fixture for tests
- [x] 3.2 Implement the loader to read `BacktestResult`, per-fold scores (from per-fold parquet sidecars), metrics, files (LLM source blobs), and params from the optimization-ledger row
- [x] 3.3 Verify the loader handles partial ledgers (no per-fold parquet) gracefully — error message names the missing artifact
- [x] 3.4 Round-trip test: optimize a tiny fixture (or hand-build a fake optimize-run dir), load it via the baseline loader, assert structural equality with the source artifacts

## 4. CLI rewrite

- [x] 4.1 Replace the `wiring_incomplete` body in `cli.py:hypothesize` with a call to a new private `_run_hypothesize(...)` that orchestrates the construction helpers and invokes the workflow
- [x] 4.2 Add new flags: `--objective`, `--llm-critic`, `--engine-worker`, `--cache-root`, `--work-root`, `--gateway-root`, `--kb-store`, `--rebuild-kb`, `--model-stage1`, `--model-stage2`, `--model-stage3`, `--model-critique`, `--model-rank`
- [x] 4.3 Add the four pre-workflow validation gates (crate exists, intent.toml exists, baseline source provided, API key set, engine-worker binary exists), each surfacing a typer error
- [x] 4.4 Implement the success-path JSON output: serialize `HypothesizeResult` (strategy, accepted, rejected, termination_reason, iterations, backtests_consumed, persisted_decision_ids) plus `baseline_source`
- [x] 4.5 Keep `--dry-run` meaningful: print the constructed dep summary (which baseline source, which models per stage, which fold count) without invoking the workflow

## 5. Tests

- [x] 5.1 `python/tests/test_hypothesize_wiring.py` — unit tests for each construction helper with stub deps; covers missing-file errors, baseline-defaults computation, KB store reuse
- [x] 5.2 Baseline loader round-trip test against a fixture optimize-run dir under `python/tests/fixtures/`
- [x] 5.3 Evaluate-fold factory test: experiment.yaml branch dispatches per-fold; smoke-only branch returns fold-0 only
- [x] 5.4 `python/tests/test_cli_hypothesize_end_to_end.py` — typer CliRunner test that monkeypatches the wiring helpers + workflow, asserts the CLI returns the new JSON envelope (not the old stub)
- [x] 5.5 Negative-path CLI tests: missing crate, missing intent.toml, no baseline flag with no optimize run, missing API key — each produces the expected typer error
- [x] 5.6 Run the existing `test_cli_author.py` and `test_author_repair_exhaustion.py` to confirm the hypothesize work hasn't regressed author CLI behavior

## 6. Documentation

- [x] 6.1 Update `docs/how-to/cli-cookbook.md` Hypothesis section: replace the "drive from Python" workaround with the real CLI usage; document the two baseline modes; document the new flags
- [x] 6.2 Update `docs/tutorials/hypothesize-loop.md` to use the CLI end-to-end against the VXX reference crate (currently it replays a fixture ledger); preserve the replay path as a separate subsection
- [x] 6.3 Update `docs/how-to/author-a-strategy.md` "Follow-up commands" to point at the working `hypothesize` invocation
- [x] 6.4 Update `CLAUDE.md` if it references the wiring-incomplete stub (it doesn't currently, but check)

## 7. Quality gates

- [x] 7.1 `make lint` clean (ruff, ruff format, mypy --strict on `python/strategy_gpt/`)
- [x] 7.2 `make test` clean; the new wiring + CLI tests are included
- [x] 7.3 Manual smoke (operator): build the engine-worker (`cd crates && cargo build -p engine-worker`), set `ANTHROPIC_API_KEY`, run `strategy-gpt hypothesize vxx --baseline-defaults --quick` end-to-end against the VXX reference crate, verify the loop completes with at least one accepted or rejected hypothesis and a JSON summary
- [x] 7.4 Manual smoke (operator): run `strategy-gpt hypothesize vxx --baseline-from <opt-run-id> --quick` after an optimize run, verify the baseline source is reflected in the summary
