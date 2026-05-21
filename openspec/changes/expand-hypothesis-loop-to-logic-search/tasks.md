## 1. Phase A — Foundations

- [x] 1.1 Author `crates/engine-rt/PROMPT_API.md` with full Strategy trait, Context handle, data types, allowed-crate list, file-layout convention, forbidden constructs, and minimal exemplar
- [x] 1.2 Choose and document the param-declaration convention (macro vs. trait-object vs. associated-const table); land convention in `PROMPT_API.md`
- [x] 1.3 Implement param-declaration support in `engine-rt` and update `vxx-strategy` + `example-strategy` to use it
- [x] 1.4 Expose `declared_param_schema(artifact)` surface from `build-pipeline`
- [x] 1.5 Add ADR `docs/decisions/0016-prompt-api-md-authoritative-llm-context.md`
- [x] 1.6 Implement per-strategy ledger layout under `ledger/strategies/<strategy_name>/{hypothesis_records.parquet, decision_records.parquet, baseline/, sources/, responses/}`
- [x] 1.7 Implement content-addressed source-blob storage helper (`ledger.write_source_set`, `ledger.read_source_set`)
- [x] 1.8 Implement `ledger.baseline_best(strategy, dataset_manifest)` with optimize-ledger lookup + on-demand compute fallback
- [x] 1.9 Add ADR `docs/decisions/0017-per-strategy-storage-layout.md`
- [x] 1.10 Write `python/strategy_gpt/markdown_io.py` — strict parser for stage-1/2/3 markdown responses, structured `ParseError` with section identification
- [x] 1.11 Unit tests for markdown_io: round-trip serialization, malformed section detection, file-block extraction, DELETE handling
- [x] 1.12 Extend `kb_query_node` with a deterministic post-retrieval filter consuming `prior_decisions` (suppress recycled, boost accepted); add unit tests
- [x] 1.13 Drop legacy `runner_version` field from hypothesis records (no versioning) and add ADR `docs/decisions/0018-no-versioning-on-hypothesis-records.md`

## 2. Phase B — Candidate generation

- [x] 2.1 Author stage-1 prompt builder (idea + rationale + lift + side effects) consuming diagnosis, KB cites, prior decisions, intra-run history
- [x] 2.2 Author stage-2 prompt builder (falsification + param_intent) consuming locked stage-1 + engine-rt API + baseline param schema
- [x] 2.3 Author stage-3 prompt builder (files map markdown) consuming locked stage-1 + stage-2 + engine-rt API + baseline source files
- [x] 2.4 Implement `ReasoningClient` for Anthropic — structured tool-use enforced shape per stage
- [x] 2.5 Implement `ReasoningClient` for OpenAI — JSON-schema enforced shape per stage
- [x] 2.6 Replace stub `_StubReasoningClient` with a dispatch layer over Anthropic + OpenAI implementations
- [x] 2.7 Implement `cheap_critique_node` — runs after stage 1, rejects malformed / duplicate / contradicts-diagnosis / violates-prior-accept
- [x] 2.8 Implement multi-stage repair loop (`K_repair = 2` per stage) with synthesized feedback (parse error / build error / smoke panic / schema mismatch)
- [x] 2.9 Wire build-pipeline + cargo lints into the stage-3 validation chain; persist attempts to `DecisionRecord.evidence`
- [x] 2.10 Add ADR `docs/decisions/0019-multi-stage-llm-emission.md`

## 3. Phase C — Candidate evaluation

- [x] 3.1 Add `tester.attempt_with_optimize(artifact, param_intent, falsification, folds, method, trials)` returning per-fold + aggregate + side-effect flags + falsification verdict
- [x] 3.2 Expand tester reject-reason taxonomy (`reject_format`, `reject_schema`, `reject_noise`, `reject_variance`, `reject_verdict`, `reject_deps`) with structured rationale per kind
- [x] 3.3 Implement `mechanical_gate_node` — score floor `(cand - baseline) > k · σ_combined` + per-fold CV check; emit borderline flag
- [x] 3.4 Implement `verdict_critique_node` — LLM review of measured result vs claim, side-effect envelope, rationale-vs-result mismatch, complexity-cost
- [x] 3.5 Replace `_complexity_penalty` with continuous complexity differential; add simplicity bonus to `rank_score`
- [x] 3.6 Extend `diagnose.py` with exit-reason histogram, missed-opportunity regions, drawdown trajectory shape, holding-period-vs-PnL histogram
- [x] 3.7 Add ADR `docs/decisions/00NN-comparative-falsification-variance-aware-epsilon.md`

## 4. Phase D — Orchestration

- [x] 4.1 Write `python/strategy_gpt/workflow.py` — assemble `langgraph.graph.StateGraph` over existing pure-fn nodes (diagnose, kb_query, kb_filter, generate_stage{1,2,3}, cheap_critique, build_and_smoke, mini_optimize, mechanical_gate, verdict_critique, rank, select)
- [x] 4.2 Define conditional edge `should_continue` after `rank` driving the inner iteration loop
- [x] 4.3 Write `python/strategy_gpt/hypothesize.py` — orchestrator entry function `hypothesize(strategy, *, ledger, kb, config, persist=True)`
- [x] 4.4 Bootstrap state from `ledger.recent_decisions(strategy=...)`; load or compute baseline-best
- [x] 4.5 Wire `persist_decisions` to per-strategy layout including source-blob writes
- [x] 4.6 Implement `strategy-gpt hypothesize <strategy>` CLI subcommand with flags `--baseline-from`, `--baseline-defaults`, `--max-backtests`, `--quick`, `--borderline-k`, `--k-candidates`, `--iteration-budget`, `--dry-run`
- [x] 4.7 Enforce `--max-backtests` ceiling at iteration start; reject iterations that would exceed budget with clear message

## 5. Phase E — Operability

- [x] 5.1 Implement hypothesize-run replay command (`strategy-gpt hypothesis replay <decision_id>`) reconstructing files from source blobs → build → mini-optimize → compare to stored evidence
- [x] 5.2 Implement `strategy-gpt hypothesis diff <decision_id>` rendering unified diff between candidate files and `baseline_files_hash` blobs
- [x] 5.3 Rewrite `python/strategy_gpt/smoke.py` to drive the full new flow end-to-end with stubbed LLM responses; preserve deterministic golden fixture
- [x] 5.4 Update CI byte-identity smoke check to cover the new flow
- [x] 5.5 Update `python/tests/test_hypothesis_loop.py` for new state shape, multi-stage emission, repair loop, mechanical gate, verdict-critique
- [x] 5.6 Add `python/tests/test_workflow_stategraph.py` validating real LangGraph wiring (edges, conditional edges, resumable state)
- [x] 5.7 Add `python/tests/test_markdown_io.py`, `python/tests/test_kb_filter.py`, `python/tests/test_mechanical_gate.py`
- [x] 5.8 Author `docs/how-to/run-hypothesize.md` (operator-facing)
- [x] 5.9 Author `docs/reference/hypothesize-cli.md` (flags + exit codes + output JSON shape)
- [x] 5.10 Author `docs/explanation/hypothesize-loop.md` extracted from `design.md` (durable explanation outside the archived change folder)
- [x] 5.11 Land all four ADRs from earlier phases under `docs/decisions/`
- [x] 5.12 Run `make lint` + `make test` clean; verify `make docs-serve` renders new pages
