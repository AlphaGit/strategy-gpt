## Context

`strategy_gpt.hypothesize.hypothesize` is the orchestrator entry for the hypothesis loop. It accepts a fully-populated `HypothesizeDeps` (KB, stage client, build pipeline, evaluate-fold, baseline artifacts, allowed metrics, kept bounds, objective metric, dataset manifest hash) and drives the LangGraph workflow end-to-end. Construction of that bag is non-trivial: every collaborator is operator-specific, and the baseline shape couples to the optimization-ledger format if loaded from a prior optimize run.

The CLI command (`python/strategy_gpt/cli.py:hypothesize`) was scaffolded in Phase D with the *flag* surface in place but the body short-circuited to a `"wiring_incomplete"` JSON envelope. The Phase-D notes punted on:

- which KB store backend to bind the CLI to,
- how to thread the engine + gateway into an `EvaluateFoldFn`,
- how to compute or load a baseline,
- how to read the strategy crate's `intent.toml` / `smoke.toml` / `experiment.yaml` into the dep bag.

This change resolves all four. The aim is not to redesign anything that already works — the orchestrator, the workflow, the per-strategy ledger, the optimization ledger, the reasoning-client router are all in place. The aim is to wire them up so a CLI invocation actually runs the loop.

## Goals / Non-Goals

**Goals:**

- `strategy-gpt hypothesize <name>` runs the full loop end-to-end against the crate at `crates/<name>-strategy/` when (a) the crate exists, (b) the operator has an API key, and (c) either an optimize run or baseline-defaults is requestable.
- Failure modes (no crate, no intent, no baseline, missing engine worker binary) surface as clear typer errors naming the next step, not as a `wiring_incomplete` stub.
- The construction helpers live outside `cli.py` so unit tests can exercise them without typer.
- Existing `--baseline-from <optimize-run>` flag works against an optimization-ledger run id.
- New `--baseline-defaults` flag short-circuits the optimize step by smoke-running the crate at default parameters and lifting the result into a baseline.
- The CLI flag surface stays backwards-compatible; new flags are additive.

**Non-Goals:**

- No KB ingestion pipeline rewrite. The CLI uses whatever store exists under `kb/store/` (or builds it from `kb/sources.toml` on first run via the existing ingestion helpers); enriching the corpus is a separate change.
- No new vendor adapters in `reasoning_clients.py`. We reuse `_StageRouter` as-is.
- No optimization-ledger format changes. The baseline loader reads existing artifacts; if the format needs to grow new fields, that's a separate proposal.
- No verdict-critique LLM client construction beyond the existing deterministic critic (LLM critic is opt-in via flag but the wiring uses the same shape).
- No live-trading hookup. The platform is a research loop.

## Decisions

### Decision: Construction helpers live in a new `hypothesize_wiring.py`, not in `cli.py`

The factories that build `KbClient`, `StageReasoningClient`, `EvaluateFoldFn`, and the baseline tuple are pure functions of inputs (crate path, env, optimize-run id, …). Putting them in a non-typer module lets unit tests call them directly, lets future programmatic callers reuse them, and keeps `cli.py` focused on flag plumbing.

**Alternative considered:** keep everything in `cli.py` and rely on `CliRunner` for tests. Rejected — `CliRunner` is slow, exercises the whole flag-parsing dance for every test, and forces tests to either build a fake LLM environment or skip integration.

### Decision: Baseline resolution has two explicit modes (`--baseline-from` and `--baseline-defaults`)

`--baseline-from <run-id>`: load `BacktestResult` + per-fold scores + metrics + files + params from the optimize ledger row. This is the standard path once an operator has at least one optimize run.

`--baseline-defaults`: invoke the same `evaluate_fold` the loop will use, with the crate's default parameter values (parsed from `intent.toml.param_schema_sketch`), to produce a fresh baseline that lives in the same metric space as the candidates. Cheaper than running optimize but less rigorous.

When neither flag is set and no optimize run is found, the CLI exits with an error message naming the two options — explicit defaults are better than picking one silently.

**Alternative considered:** auto-fall-back to `--baseline-defaults` when no optimize run is found. Rejected — silently picking a worse baseline can corrupt downstream rationale ("hypothesis beat baseline" loses meaning if the baseline is just smoke-run defaults). Make the operator choose.

### Decision: Evaluate-fold factory uses `experiment.yaml` when present, smoke fallback when not

When `crates/<name>-strategy/experiment.yaml` exists (operator ran `author --verify=batch`), the factory reads the fold scheme from it and dispatches one engine batch per fold. When it doesn't exist, the factory builds a single-fold evaluator from `smoke.toml`: one engine submission over the smoke window, results returned as fold 0.

This is intentionally lenient. A freshly-authored crate that only has `smoke.toml` can still be hypothesized — operators don't have to run optimize first. The loop's mechanical gate (variance-aware floor) just becomes less informative with a single fold, which is the correct outcome (less evidence → less confidence → narrower acceptance window).

**Alternative considered:** require `experiment.yaml` and error out without it. Rejected — too high a bar for a research loop whose explicit purpose is to lower the cost of iteration.

### Decision: KB store path is configurable but defaults to `kb/store/`

The default lets `strategy-gpt hypothesize <name>` work out-of-the-box from a fresh checkout. Operators with a custom corpus point at it via `KB_STORE_PATH` env var or `--kb-store <path>` flag. If the store doesn't exist on first run, the wiring helper invokes the existing ingestion path against `kb/sources.toml` and persists. Subsequent runs reuse the persisted store.

**Alternative considered:** require explicit `--kb-store`. Rejected — operators who haven't engaged with the KB yet should still be able to run the loop (the workflow tolerates a sparse KB; retrieval just returns fewer citations). Failing on first run for a missing-store would push operators away.

### Decision: Failure surface is typer errors, not the JSON `wiring_incomplete` envelope

Every failure mode the CLI can detect before invoking the workflow gets a typed message via `typer.echo(..., err=True); raise typer.Exit(code=2)`:

- crate dir missing → `crates/<name>-strategy/ does not exist; run 'strategy-gpt author <name>' first`
- `intent.toml` missing → `crates/<name>-strategy/intent.toml not found; the strategy has not been authored cleanly`
- both `--baseline-from` and `--baseline-defaults` set → typer.BadParameter (existing)
- neither set and no optimize-run found → `no baseline provided; pass --baseline-from <optimize-run-id> or --baseline-defaults`
- no API key set → `set ANTHROPIC_API_KEY or OPENAI_API_KEY before running hypothesize`
- engine worker binary missing → `engine-worker binary not found at <path>; build it via 'cd crates && cargo build -p engine-worker'`

The JSON `wiring_incomplete` envelope goes away entirely.

**Alternative considered:** keep the JSON envelope but populate it with the same diagnostics. Rejected — the envelope was a placeholder for "this command is not done yet"; once it works, errors should look like errors and successes should look like successes (JSON `HypothesizeResult` summary on success).

### Decision: Stage models default per-stage, overridable via `--model-<stage>` flags

Per the existing `reasoning_clients._StageRouter` shape: stage 1 (rewrite) and stage 2 (logic search) get expensive models; stage 3 (translate-to-files) gets a cheaper model; critique and rank get the cheapest. Default to environment-resolved defaults (existing helper). Per-stage `--model-stage1`, `--model-stage2`, `--model-stage3`, `--model-critique`, `--model-rank` flags let operators override.

This keeps the cost model predictable without forcing operators to know about it.

## Risks / Trade-offs

- **Risk:** Baseline-defaults can mislead. A baseline computed from default params on a single smoke fold is a thin reference; comparisons against it may inflate hypothesis quality. → **Mitigation:** label the baseline source in the per-strategy ledger record (`baseline_source: optimize_run | baseline_defaults`) and surface it in the CLI summary so operators see what they're comparing against. Document the trade-off in the how-to.
- **Risk:** Single-fold smoke fallback for the evaluate-fold factory means the mechanical gate's variance floor is computed over n=1, which has no meaningful variance. → **Mitigation:** the gate falls back to a fixed-margin threshold when the fold count is below the minimum-for-variance (≥3). The gate code already handles n=1; this change just exposes that path more often. Document the behavior.
- **Risk:** KB store auto-build on first run can be slow (corpus ingestion). → **Mitigation:** print a one-time progress banner with an estimate, gate behind a `--rebuild-kb` flag once the store exists, and surface ingest errors clearly. The ingestion path is bounded by the corpus size already.
- **Risk:** Calling the LLM costs money; an operator who didn't realize the CLI now runs the real loop could spend unexpectedly. → **Mitigation:** the existing `--dry-run` flag stays (now actually meaningful: prints what would be invoked without making any LLM call), `--quick` keeps the small-budget mode, and the CLI prints the configured budgets up front so the operator can Ctrl-C.
- **Trade-off:** Inlining the construction in `cli.py` is shorter; extracting to `hypothesize_wiring.py` is more testable. Picked testability; the extra module is small.
- **Trade-off:** New flags (per-stage model, objective, kb-store) widen the surface. Picked the wider surface; the existing flags carry over verbatim so backwards compat is preserved.

## Migration Plan

- The `wiring_incomplete` JSON envelope is removed. Any operator scripts grepping for `"status": "wiring_incomplete"` need to switch to checking the typer exit code and parsing the new JSON shape. Since the envelope was explicitly a placeholder ("drive from Python"), no scripts should rely on it; the tutorial that documented the workaround is updated in this change.
- No data-on-disk migration. The per-strategy ledger format, the optimization ledger format, and the KB store format are unchanged.
- The flag surface grows but no existing flag's meaning changes. Operators using the existing flags continue to work.

## Open Questions

- **Question:** Should `--baseline-defaults` write a record into the optimization ledger so subsequent `--baseline-from` calls can reference it? → Tentative answer: no, not in this change. Baseline-defaults is the cheap path; optimize is the rigorous path. Conflating them in the ledger would let operators accidentally treat a defaults baseline as an optimize result. Revisit if operators ask for it.
- **Question:** Should `--kb-store` default to a per-strategy KB (`ledger/strategies/<name>/.kb/`) instead of the shared `kb/store/`? → Tentative answer: keep it shared. The KB carries general quantitative-research knowledge, not strategy-specific facts. Per-strategy isolation would duplicate the corpus and break cross-strategy citation comparison.
- **Question:** Should the LLM critic option carry a model flag? → Yes if we add the flag; tentative default `claude-haiku-4-5-20251001` for cost. Tracked as a follow-up if not landed in this change.
