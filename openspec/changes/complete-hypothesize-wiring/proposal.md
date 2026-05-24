## Why

The `strategy-gpt hypothesize <strategy>` CLI is the operator-facing entry to the hypothesis-loop research engine — the whole point of the platform after a strategy has been authored. Today the subcommand resolves its flags, prints a JSON summary, and exits with a `"wiring_incomplete"` envelope instructing the operator to drive the loop from Python with a hand-built `HypothesizeDeps`. That message has been in place since Phase D; it has stayed there because constructing the dep bag requires operator-specific decisions (which KB store, which baseline source, how to translate the strategy crate's smoke fixture into a fold evaluator). Until the CLI itself answers those questions, the loop is unusable from the terminal — operators with a freshly-authored crate cannot exercise the loop without writing throwaway Python.

## What Changes

- Build `HypothesizeDeps` end-to-end inside the CLI command from the named strategy crate and the operator's environment, replacing the `wiring_incomplete` stub with a real call to `strategy_gpt.hypothesize.hypothesize`.
- **KB client**: construct a `KbClient` backed by the SQLite-resident hybrid-retrieval store under `kb/`, building the index lazily on first hypothesize run if the index is absent. Honor `KB_STORE_PATH` env var to point at a different store; default `kb/store/`.
- **Stage reasoning client**: reuse `reasoning_clients._StageRouter` (the Anthropic+OpenAI two-vendor router that already lives in the codebase) constructed from `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` and per-stage model defaults; expose `--model` overrides per stage.
- **Build pipeline**: reuse the same `BuildPipeline` construction the `author` CLI uses (`crates_dir` + `cache_root` + `work_root` resolved to absolute paths).
- **Evaluate-fold factory**: build an `EvaluateFoldFn` closure over `Engine` + `Gateway` + the strategy crate's smoke/experiment bars. When the crate has an `experiment.yaml`, fold bounds come from it; otherwise the smoke window becomes a single-fold evaluator. The factory submits a small batch per fold and returns the metrics dict the loop consumes.
- **Baseline resolution**: two paths.
  - `--baseline-from <optimize-run-id>` reads the optimize-run's `best.json` + per-fold artifacts from the optimization ledger and lifts them into a `BacktestResult` + per-fold-scores + metrics tuple.
  - `--baseline-defaults` (or default when neither flag is set and no optimize run is available) builds the baseline by smoke-running the crate at its default parameters (using the same evaluate_fold the loop will use), so the baseline lives in the same metric space as the candidates.
- **Other knobs**: resolve `dataset_manifest_hash` from the gateway response, `allowed_metrics` from a small canonical set + the strategy's `intent.toml`, `kept_bounds` from `intent.toml`'s `param_schema_sketch` (min/max per param), `objective_metric` from a new `--objective` flag (default `sharpe_ratio`).
- **Verdict critic**: default to `DeterministicVerdictCritic`; the LLM-backed critic is opt-in via `--llm-critic`.
- **Failure surface**: when the strategy crate cannot be resolved, has no `Cargo.toml`, has no `intent.toml`, or its `smoke.toml` references a missing data slice, fail with a clear typer message naming the missing artifact and the next step (run `author` first, run `optimize` first, etc.) — no more `wiring_incomplete` placeholder.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `hypothesis-loop`: add a requirement covering the CLI wiring contract (what `strategy-gpt hypothesize` must construct from the operator's environment, how baselines are resolved, what flags surface), and a requirement covering the evaluate-fold factory's contract (single-fold smoke fallback when no `experiment.yaml` is present, multi-fold dispatch when one is). The hypothesis-loop spec already covers the workflow internals; this change documents the *entry-point* contract that's been a stub.

## Impact

- Modified: `python/strategy_gpt/cli.py` — replace the `hypothesize` subcommand body. Add `_build_hypothesize_deps(strategy, ...)` factory that materializes the dep bag and a thin error-translation layer for the failure surfaces enumerated above.
- New: `python/strategy_gpt/hypothesize_wiring.py` — extracted construction helpers (KB factory, stage-client factory, evaluate-fold factory, baseline loader, baseline-defaults computation). Kept out of `cli.py` so tests can drive the helpers without invoking typer.
- Possible new: `python/strategy_gpt/baseline_loader.py` — thin module that reads optimize ledger artifacts and lifts them into the shape `HypothesizeDeps` wants. May fold into `hypothesize_wiring.py` if it's small enough.
- Modified: `python/strategy_gpt/kb_query.py` (if the KB factory exposes a `KbClient.from_path` constructor it doesn't have yet).
- New tests under `python/tests/`:
  - `test_hypothesize_wiring.py` — unit tests for each factory helper, baseline-loader round-trip from a fixture optimize-run, evaluate-fold smoke against the VXX reference crate.
  - `test_cli_hypothesize_end_to_end.py` — typer CliRunner test that monkeypatches the factories and asserts the CLI produces a `HypothesizeResult`-shaped JSON envelope (not the old `wiring_incomplete` stub).
- Docs:
  - Update `docs/how-to/cli-cookbook.md` Hypothesis section to drop the "drive from Python" workaround and document the real flags.
  - Update `docs/tutorials/hypothesize-loop.md` to use the CLI end-to-end (the current tutorial replays a fixture ledger; this change lets it also exercise a live loop against the VXX reference crate).
  - Update `CLAUDE.md` if it references the wiring-incomplete stub.
- No new external dependencies. KB index construction reuses what's under `kb/` plus the SQLite stdlib. No new Rust crates touched.
- Backwards-compatible at the CLI flag surface: existing flags (`--baseline-from`, `--baseline-defaults`, `--max-backtests`, `--quick`, `--borderline-k`, `--k-candidates`, `--iteration-budget`, `--dry-run`) keep their meaning; new flags `--model-{stage1,stage2,stage3,critique,rank}`, `--objective`, `--llm-critic`, `--engine-worker`, `--cache-root`, `--work-root`, `--gateway-root` are additive with sensible defaults.
