## Why

The current `hypothesis-loop` capability is scoped to diagnose a single backtest and propose parameter or small-logic tweaks. That scope is wrong for the platform's research goal: the loop should generate *strategy-logic ideas* — new components, replacement subsystems, structural mutations — informed by how the current strategy behaves, what the curated KB contains, and what has been tried before. Parameter tuning is the job of `optimize`; logic search is a distinct concern and the existing spec does not describe it.

Today's partial implementation under `python/strategy_gpt/{diagnose,kb_query,nodes,hypothesis_loop,reasoning}.py` is a single-pass LLM critique over a stubbed reasoning client, with nodes wired as pure functions rather than a LangGraph workflow. There is no candidate compilation, no per-candidate evaluation, no comparative falsification against a baseline, no repair loop, and no per-strategy persistence. The CLI surface (`strategy-gpt hypothesize`) is a stub.

This change rewrites `hypothesis-loop` as a strategy-logic search loop, integrates the build-pipeline + tester for in-loop candidate evaluation, adds a variance-aware mechanical gate, and lays out per-strategy ledger storage so each strategy accumulates its own audit-able history of accepted and rejected logic ideas.

## What Changes

- **BREAKING**: `hypothesis-loop` purpose statement and most requirements rewrite. The loop input is now a strategy crate (not a `BacktestResult`); a baseline result is derived from the optimize-ledger or computed on demand.
- **BREAKING**: `HypothesisRecord.proposed_change` adopts a structured shape (files_manifest, deleted_files, param_intent, comparative falsification with primary + guards + scope, ≤500-char rationale, stage_response hashes, `baseline_files_hash`). Drops the prior "name + metric + falsification" tuple.
- **BREAKING**: Persistence layout moves to `ledger/strategies/<strategy_name>/` with content-addressed source blobs under `sources/` and per-decision LLM response blobs under `responses/<decision_id>/`.
- Add multi-stage LLM emission for candidates: idea+rationale → commitments (falsification + param_intent) → files. Earlier stages locked once accepted; repair loop scoped per stage with `K_repair = 2`.
- Add a cheap-critique node after stage 1 (idea-level reject; saves stage 2/3 LLM cost on bad ideas).
- Add per-candidate mini-optimize over LLM-supplied `param_intent` bounds (default sobol, 64 trials), reusing the experiment-spec folds.
- Add a deterministic mechanical gate (variance-aware score floor with `k·σ_combined` + per-fold CV bound) as a non-negotiable hard floor.
- Add a verdict-critique LLM node that reviews actual results vs the stated claim and rejects on side-effects / rationale mismatch / complexity-cost asymmetry. Cannot override the mechanical gate.
- Extend the rank score with a continuous complexity differential and a simplicity bonus for net parameter / component removals.
- Add a markdown emit + parse contract for LLM output: code-fenced files (`## src/lib.rs` then fenced block) with embedded YAML blocks for structured fields. Strict parser; any malformed section hard-rejects.
- Enforce the existing `allowed_crates` whitelist on candidate Cargo.toml; no review path. Dependency suggestions are NOT supported.
- Extend `kb_query` with a prior-decision-aware post-retrieval filter: suppress chunks already cited by rejected hypotheses; boost chunks cited by accepted hypotheses.
- Extend `tester` with an `attempt_with_optimize(artifact, param_intent, falsification, folds, method, trials)` surface returning result + per-fold spread + comparative falsification verdict + side-effect flags.
- Add a `strategy-runtime` requirement that engine-rt SHALL ship a hand-maintained `PROMPT_API.md` document as the single source of truth for hypothesize generate prompts; add a named param-declaration convention so build-pipeline can introspect declared parameters.
- Add a `build-pipeline` requirement to emit declared parameter schema (names, types, bounds) from a compiled strategy artifact, consumed by tester for `param_intent` schema validation.
- Add a real LangGraph `StateGraph` assembly for the workflow (today nodes are pure functions called sequentially).
- Add a `strategy-gpt hypothesize <strategy>` CLI surface that drives the new loop.
- Add real `ReasoningClient` implementations for Anthropic and OpenAI, replacing the stub-only client surface.
- Explicitly OUT OF SCOPE: live trading, broker integrations, runner-version pinning on hypothesis records, multi-version engine-rt support, dependency review workflows, CSCV/PBO overfit detection inside hypothesize (sample size too small; deferred to downstream optimize).

## Capabilities

### New Capabilities

<!-- None. This change introduces no new capabilities; all behavior maps to existing ones. -->

### Modified Capabilities

- `hypothesis-loop`: rewrites purpose + most requirements to describe a strategy-logic search loop with multi-stage emission, mini-optimize per candidate, mechanical gate, verdict-critique, comparative falsification, simplicity-preferring rank, and the markdown emit contract.
- `tester`: adds `attempt_with_optimize` surface; expands reject-reason taxonomy (`reject_schema`, `reject_noise`, `reject_variance`, `reject_verdict`).
- `knowledge-base`: adds the prior-decision-aware retrieval filter contract.
- `experiment-ledger`: adopts the per-strategy storage layout; adds content-addressed source-blob persistence and baseline-best caching.
- `strategy-runtime`: adds the `PROMPT_API.md` authoritative-LLM-context requirement, the named param-declaration convention, and the build-pipeline param-schema introspection surface.

## Impact

- **Python**: substantial work in `python/strategy_gpt/`. Files affected: `hypothesis_loop.py` (state shape grows), `diagnose.py` (extends with exit-reason histogram, missed-opportunity regions, drawdown shape), `kb_query.py` (filter), `nodes.py` (add cheap_critique / mechanical_gate / verdict_critique / mini_optimize wrappers; rebuild generate as 3-stage orchestrator), `reasoning.py` (real clients), `tester.py` (`attempt_with_optimize`), `ledger.py` (per-strategy layout + source blob storage), `cli.py` (`hypothesize` subcommand), `smoke.py` (rewrite to drive new flow). New: `workflow.py` (LangGraph `StateGraph` assembly), `reasoning_clients/{anthropic,openai}.py`, `markdown_io.py` (emit/parse contract), `hypothesize.py` (orchestrator entry).
- **Rust**: `crates/engine-rt/` gains a hand-maintained `PROMPT_API.md` document and a `StrategyParams` declaration convention (likely `#[derive(StrategyParams)]` macro or trait-object equivalent). `crates/build-pipeline/` exposes a param-schema introspection surface for compiled artifacts. `crates/vxx-strategy/` and `crates/example-strategy/` updated to adopt the new param-declaration convention.
- **Ledger**: per-strategy folder layout `ledger/strategies/<strategy_name>/` with `hypothesis_records.parquet`, `decision_records.parquet`, `baseline/best.json`, content-addressed `sources/<files_set_hash>/`, and `responses/<decision_id>/stage{1,2,3}.md` + `repair_*.md`. **BREAKING**: existing ledger entries (if any) need migration or one-time discard.
- **Dependencies**: real LangGraph wiring activates the already-declared `langgraph>=0.2` dep; Anthropic + OpenAI SDKs already optional via `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`. No new dependency additions in this change.
- **CLI**: `strategy-gpt hypothesize <strategy>` becomes functional. Flags: `--baseline-from <opt_id>`, `--baseline-defaults`, `--max-backtests N`, `--quick` (small trial budget), `--borderline-k <float>`, `--k-candidates N`, `--iteration-budget N`.
- **Tests**: full new test surface for parse / repair / multi-stage / mechanical-gate / verdict-critique. Existing tests under `python/tests/test_hypothesis_loop.py` rewrite to cover the new state shape + flow.
- **Docs**: new `docs/how-to/run-hypothesize.md`, `docs/reference/hypothesize-cli.md`, `docs/explanation/hypothesize-loop.md` (extracted from design.md on archive), and ADRs for multi-stage emission, comparative falsification with variance-aware ε, per-strategy storage layout, and PROMPT_API.md as authoritative LLM context.
- **Smoke fixture**: `smoke.py` rewrite + golden fixture update. Existing CI byte-identity smoke check still gates merges; new flow must remain deterministic given fixed stub LLM responses.
