# Spec: hypothesis-loop

## Purpose

LangGraph-orchestrated reasoning loop that diagnoses backtest results, queries the knowledge base, generates and self-critiques candidate hypotheses, ranks them, and persists an accept/reject decision log. Every accepted hypothesis carries citations and a falsification criterion that the Tester can verify against backtest output.
## Requirements
### Requirement: LangGraph workflow with explicit nodes

The Hypothesis Loop SHALL be implemented as a LangGraph `StateGraph` workflow with the following nodes, in this order: `diagnose`, `kb_query`, `kb_filter`, `generate_stage1_idea`, `cheap_critique`, `generate_stage2_commitments`, `generate_stage3_files`, `build_and_smoke`, `mini_optimize`, `mechanical_gate`, `verdict_critique`, `rank`, `select`. State transitions between nodes MUST be explicit and observable; the graph MUST be a real `langgraph.graph.StateGraph` instance (not a sequence of pure-function calls) so the workflow can be introspected, paused, and resumed.

#### Scenario: Workflow executes node sequence

- **WHEN** a strategy is submitted to the loop
- **THEN** `diagnose` runs first against a baseline `BacktestResult`, followed by `kb_query`, `kb_filter`, the inner `generate_stage1_idea → cheap_critique → generate_stage2_commitments → generate_stage3_files → build_and_smoke → mini_optimize → mechanical_gate → verdict_critique` iteration, and finally `rank` and `select`, with state visible at each transition

#### Scenario: Graph wiring is a real StateGraph

- **WHEN** the workflow is constructed
- **THEN** it is a `langgraph.graph.StateGraph` instance with explicit edges and conditional edges, NOT a Python function that calls nodes sequentially

### Requirement: Internal iteration loop

The workflow SHALL loop through `generate_stage1_idea → cheap_critique → generate_stage2_commitments → generate_stage3_files → build_and_smoke → mini_optimize → mechanical_gate → verdict_critique → rank` until at least K hypotheses pass verdict-critique, an iteration budget is exhausted, or candidate similarity to prior items crosses a configured threshold. The termination reason MUST be recorded.

#### Scenario: Loop terminates on sufficient candidates

- **WHEN** the verdict-critique node has accepted K hypotheses
- **THEN** the loop exits and `select` proceeds with the accepted set, recording `terminated: sufficient_candidates`

#### Scenario: Loop terminates on budget exhaustion

- **WHEN** the iteration budget is reached without K accepted hypotheses
- **THEN** the loop exits with the partial accepted set and records `terminated: budget_exhausted`

#### Scenario: Loop terminates on similarity saturation

- **WHEN** every newly generated stage-1 idea resembles a prior rejected candidate above the configured similarity threshold
- **THEN** the loop exits and records `terminated: similarity_saturation`

### Requirement: Knowledge base queries with citation capture

The `kb_query` node SHALL retrieve relevant concepts, indicators, regimes, models, and techniques from the Knowledge Base and attach citations to each generated hypothesis. Citations MUST include source provenance (book, page or paper, section). The retrieval results SHALL then pass through a `kb_filter` node that consults `prior_decisions` to suppress chunks already cited by rejected hypotheses and to boost chunks cited by accepted hypotheses; the filter MUST be deterministic and operate without any LLM call.

#### Scenario: Hypothesis carries citations

- **WHEN** the `generate_stage1_idea` node produces a candidate informed by a KB retrieval
- **THEN** the candidate record contains a list of `kb_cites` with source provenance

#### Scenario: Recycled chunk is suppressed

- **WHEN** a retrieved chunk's `(source, locator)` appears in the `kb_cites` of any rejected `prior_decision`
- **THEN** the `kb_filter` node drops or score-discounts that chunk before stage 1 generates the idea

#### Scenario: Accepted-cite chunk is boosted

- **WHEN** a retrieved chunk's `(source, locator)` appears in the `kb_cites` of any accepted `prior_decision`
- **THEN** the `kb_filter` node uplifts that chunk's effective score so generate tends toward productive neighborhoods

### Requirement: Decision log persistence

Every accepted hypothesis SHALL be persisted with rationale, evidence, KB citations, the content-addressed source-blob reference, the comparative-falsification verdict, and timestamp. Every rejected hypothesis SHALL be persisted with its rejection stage (`cheap_critique`, `build`, `lint`, `schema`, `smoke`, `mechanical_gate`, `verdict_critique`), rejection rationale, the repair-attempt chain if any, and timestamp. The decision log MUST be stored in the experiment ledger under the per-strategy subfolder `ledger/strategies/<strategy_name>/` and re-loaded as context on subsequent runs of the same strategy.

#### Scenario: Past rejected ideas inform future rejections

- **WHEN** a new candidate's stage-1 idea closely resembles a previously rejected one
- **THEN** the `cheap_critique` node reads the prior rejection rationale from the ledger and accounts for it

#### Scenario: Repair-attempt chain persists

- **WHEN** a candidate undergoes one or more repair attempts at stage 3 before final acceptance or rejection
- **THEN** the persisted `DecisionRecord.evidence` includes an `attempts` array with one entry per repair attempt, recording the attempted `files_hash`, the reject kind, and the synthesized error feedback

### Requirement: Hypothesis output schema

Each hypothesis emitted by the loop SHALL include: a `candidate_name` slug, the parent `strategy` name, a `files_manifest` (path → content-addressed blob hash), a `deleted_files` list, a `baseline_files_hash` anchor, a `param_intent` block (with `added`, `kept`, and `removed` parameter sets and bounds for added params), a comparative `falsification` block (primary claim with metric, direction, delta_vs_baseline, and scope, plus zero or more guard constraints), an `expected_lift_confidence` in `[0, 1]`, an `expected_side_effects` bullet list, a `rationale` field capped at 500 characters, and a `stage_responses` map with hashes referencing the three stage emission blobs.

#### Scenario: Tester receives a fully specified hypothesis

- **WHEN** the loop emits a hypothesis to the Tester for evaluation
- **THEN** the hypothesis record contains all required fields and the Tester can decide acceptance/rejection without further input from the loop

#### Scenario: Rationale exceeds 500 characters

- **WHEN** the LLM produces a stage-1 rationale longer than 500 characters
- **THEN** the parsed `rationale` field is truncated to 500 characters and the full text is preserved in the `stage1_idea.md` blob referenced by `stage_responses.stage1_hash`

### Requirement: Reasoning model usage

The `cheap_critique`, `generate_stage1_idea`, `generate_stage2_commitments`, `generate_stage3_files`, and `verdict_critique` nodes SHALL use a reasoning-capable model. The model is configurable but the workflow MUST default to the most capable reasoning model available at runtime, selected by the existing `select_reasoning_model` policy.

#### Scenario: Configured model is honored

- **WHEN** the workflow is configured to use a specific model
- **THEN** all reasoning calls in the listed nodes use that model

### Requirement: Multi-stage candidate emission

Candidates SHALL be emitted by the reasoning model in three locked-progression stages. Stage 1 emits the candidate name, the rationale (≤500 chars), an expected lift confidence in `[0, 1]`, and an `expected_side_effects` bullet list. Stage 2 emits the `falsification` block (primary claim + guard constraints + scope) and the `param_intent` block (added with bounds, kept, removed). Stage 3 emits the file map in markdown form (code-fenced blocks per file path, with `## DELETE: <path>` headers for file removals). The output of any earlier stage MUST be locked as immutable context for later stages and for any subsequent repair attempts; stages MUST NOT be re-opened by the repair loop.

#### Scenario: Stage 3 sees stage 1 and stage 2 as locked context

- **WHEN** the `generate_stage3_files` node runs
- **THEN** its prompt contains the verbatim stage-1 and stage-2 outputs as immutable context

#### Scenario: Repair scoped to one stage

- **WHEN** a stage-3 build failure triggers a repair attempt
- **THEN** the repair prompt re-runs only stage 3 with the prior attempt's files and build errors attached; stages 1 and 2 are not re-emitted

### Requirement: Markdown emit and parse contract

The LLM SHALL emit each stage's response as markdown. Structured metadata SHALL be encoded in YAML inside fenced code blocks under named H1 sections. Stage 3 file content SHALL be emitted as `## <path>` H2 headers followed by a fenced code block containing the file content; file deletions SHALL be encoded as `## DELETE: <path>` headers without a code block. The parser MUST be strict: any malformed or missing required section MUST hard-reject the candidate with a `reject_format` outcome whose rationale identifies the offending section.

#### Scenario: Stage 3 emission contains an unparseable file block

- **WHEN** a stage-3 response contains an H2 header for a file path but no following fenced code block
- **THEN** the parser raises `reject_format` and the repair loop is invoked with a "missing code block for `<path>`" feedback message

#### Scenario: YAML metadata block has invalid keys

- **WHEN** a stage-2 emission's `Falsification` YAML block uses a metric name not in `BacktestMetrics`
- **THEN** the parser raises `reject_format` and the repair loop is invoked with the list of allowed metric names

### Requirement: Repair loop per stage

Each generate stage SHALL support a bounded repair loop. The repair budget MUST default to `K_repair = 2` attempts per stage and SHALL be configurable. Repair attempts MUST be persisted to the experiment ledger as an `attempts` array on the associated `DecisionRecord.evidence`. After exhausting the repair budget for a stage, the candidate MUST be hard-rejected with the outcome `exhausted_repair_budget`. The repair loop SHALL apply only to structural failures (`reject_format`, `reject_build`, `reject_lint`, `reject_schema`, `reject_smoke`). Mechanical-gate failures and verdict-critique rejections MUST NOT trigger repair attempts.

#### Scenario: Build error triggers stage-3 repair

- **WHEN** a stage-3 candidate fails `cargo build`
- **THEN** a repair prompt re-runs only stage 3 with the prior attempt's files and the first three `rustc` errors as structured feedback; this counts as one of the K_repair attempts

#### Scenario: Mechanical-gate rejection does not trigger repair

- **WHEN** a candidate passes build, smoke, and schema but fails the mechanical gate
- **THEN** the candidate is rejected with `reject_noise` or `reject_variance` and no repair attempt is made

### Requirement: Cheap-critique runs after stage 1

The `cheap_critique` node SHALL run immediately after a successful stage-1 emission and BEFORE stages 2 and 3 are invoked. The node MUST reject candidates whose stage-1 idea is malformed, duplicates a prior rejected hypothesis above a similarity threshold, contradicts the diagnosis, or violates a constraint declared in an accepted prior decision. Survivors proceed to stage 2.

#### Scenario: Duplicate of prior reject

- **WHEN** a stage-1 idea's tokenized signature exceeds the similarity threshold against any prior rejected hypothesis
- **THEN** the `cheap_critique` node rejects the candidate before stage 2 is invoked, persisting the duplicate's `decision_id` in the rejection rationale

### Requirement: Mini-optimize per candidate

Each candidate surviving build, lint, schema, and smoke checks SHALL be evaluated by running a mini-optimization pass over its declared `param_intent`. The search SHALL respect the LLM-supplied bounds for `added` parameters and the experiment-spec bounds for `kept` parameters; `removed` parameters MUST be absent from the search space. The mini-optimize method SHALL default to `sobol` with 64 trials per candidate and SHALL reuse the folds declared in the experiment-spec. The best-found result per fold and the aggregate score MUST be recorded on the persisted `DecisionRecord.evidence`.

#### Scenario: Default mini-optimize budget applies

- **WHEN** no explicit configuration overrides the mini-optimize budget
- **THEN** each candidate runs 64 sobol trials over its `param_intent.added` bounds plus experiment-spec bounds for kept parameters

#### Scenario: Removed param is absent from search

- **WHEN** a candidate's `param_intent.removed` lists `trail_stop_atr_mult`
- **THEN** the mini-optimize search space does not include `trail_stop_atr_mult` and the resulting `best_params` MUST NOT contain that key

### Requirement: Mechanical gate is a hard floor

After mini-optimize, the `mechanical_gate` node SHALL apply two deterministic checks. The score-floor check SHALL accept only if `(candidate_score - baseline_score) > k · σ_combined`, where `σ_combined = sqrt(σ_candidate² + σ_baseline²)` over per-fold best scores and `k` defaults to `1.0`. The variance-floor check SHALL accept only if the per-fold coefficient of variation is below a configured threshold (default `0.5`). Both checks MUST pass for the candidate to proceed to verdict-critique. Mechanical-gate rejections MUST NOT be overridable by any downstream node.

#### Scenario: Marginal candidate survives gate with borderline flag

- **WHEN** the candidate's `(cand - baseline)` is greater than `k · σ_combined` by less than 20% of that gap
- **THEN** the candidate proceeds to verdict-critique with `mechanical_gate.borderline = true` recorded on the state

#### Scenario: High fold-variance candidate is rejected

- **WHEN** the per-fold CV of the candidate's mini-optimize best scores exceeds the configured threshold
- **THEN** the candidate is rejected with `reject_variance` and verdict-critique is not invoked

### Requirement: Comparative falsification verdict

The Tester SHALL verify falsification against the baseline-best result for the same strategy and dataset_manifest. Both the LLM-stated primary claim AND the mechanical gate MUST be evaluated. A candidate SHALL be considered `falsified` if the primary claim is not met while the score floor is met; `noise` if the score floor is not met while the claim is met; and `accepted` (subject to verdict-critique) only if both pass. Guard constraint failures MUST be reported alongside the primary verdict and MUST classify the candidate as `regression` regardless of primary-claim outcome.

#### Scenario: Claim met but drawdown guard fails

- **WHEN** the primary claim of `+0.20 sharpe` is met but the guard `max_drawdown delta_vs_baseline 0.05` is exceeded
- **THEN** the candidate is rejected as `regression` and verdict-critique is not invoked

#### Scenario: Score floor failed and claim met

- **WHEN** the primary claim is met but `(cand - baseline) < k · σ_combined`
- **THEN** the candidate is rejected with `reject_noise`; the rejection rationale explicitly cites the variance-aware floor

### Requirement: Verdict-critique with no gate override

After a successful mechanical gate, the `verdict_critique` node SHALL invoke a reasoning model to review the candidate against actual measured results (aggregate metrics, per-fold spread, regime breakdown, exit-reason changes, side-effect flags). The node MAY reject the candidate for reasons including: unintended side-effects (e.g., trade count out of `expected_side_effects` envelope), rationale-vs-result mismatch (the gain came from a regime the rationale did not predict), complexity-cost asymmetry (small gain at large LoC or parameter delta), or apparent overfit signatures. The node MUST NOT override or revert a mechanical-gate rejection.

#### Scenario: Side effect outside envelope

- **WHEN** the LLM-claimed `expected_side_effects` predicted a 30% trade-count increase but the measured increase is 250%
- **THEN** the `verdict_critique` node MAY reject the candidate with rationale citing the side-effect envelope breach

### Requirement: Simplicity-preferring rank

The `rank` node's score SHALL include a continuous complexity differential and a simplicity bonus. The complexity differential MUST be computed as `delta_params + delta_components` (added minus removed). The score SHALL apply a penalty proportional to net additions and a bonus proportional to net removals so that, all else equal, simpler candidates outrank more complex ones. The exact weights SHALL be configurable; default weights are `lift=0.55, evidence=0.25, complexity_penalty=0.15, simplicity_bonus=0.05`.

#### Scenario: Tie broken by simplicity

- **WHEN** two candidates have the same measured lift and citation count, but one removes two parameters while the other adds two
- **THEN** the candidate removing parameters ranks higher

### Requirement: Allowed-crate whitelist strictly enforced

The Hypothesis Loop SHALL emit prompts that explicitly forbid the LLM from declaring crates outside the existing allowed-crate whitelist. Candidates whose generated `Cargo.toml` declares an unlisted crate MUST be hard-rejected via the existing build-pipeline allowed-crate check. The repair loop MAY re-prompt the LLM with a "remove the unlisted dependency" feedback message; the workflow MUST NOT carry, collect, or surface any "dependency suggestion" field.

#### Scenario: LLM emits a candidate that depends on `tokio`

- **WHEN** the stage-3 emission's Cargo.toml declares `tokio` (not on the whitelist)
- **THEN** the candidate is rejected with `reject_deps`; the repair loop's next attempt receives a "remove `tokio` and use only allowed_crates" instruction

### Requirement: CLI constructs HypothesizeDeps end-to-end

The `strategy-gpt hypothesize <name>` CLI SHALL construct a fully-populated `HypothesizeDeps` from the named strategy crate and the operator's environment, then invoke `strategy_gpt.hypothesize.hypothesize` and print the result envelope as JSON. The CLI MUST NOT emit a `"status": "wiring_incomplete"` placeholder; every failure mode detectable before invoking the workflow MUST surface as a typer error naming the missing artifact and the next step. The construction helpers MUST live in a non-typer module (e.g. `python/strategy_gpt/hypothesize_wiring.py`) so unit tests can exercise them without invoking the CLI.

#### Scenario: Happy path runs the workflow and prints a HypothesizeResult

- **WHEN** the operator runs `strategy-gpt hypothesize spy-atr` against a crate that has been authored cleanly, with an API key set and either an optimize run available or `--baseline-defaults`
- **THEN** the CLI builds `HypothesizeDeps`, invokes the workflow, and prints a JSON envelope carrying `strategy`, `accepted`, `rejected`, `termination_reason`, `iterations`, `backtests_consumed`, and `persisted_decision_ids` — the same shape `HypothesizeResult` serializes to

#### Scenario: Crate directory does not exist

- **WHEN** the operator runs `strategy-gpt hypothesize unknown` and `crates/unknown-strategy/` does not exist
- **THEN** the CLI exits non-zero with a stderr message identifying the missing crate and suggesting `strategy-gpt author unknown` as the next step; no `wiring_incomplete` envelope is printed

#### Scenario: No baseline provided and no optimize run available

- **WHEN** the operator omits both `--baseline-from` and `--baseline-defaults` and no optimize-run row exists for the strategy
- **THEN** the CLI exits non-zero with a stderr message naming the two baseline-source options (`--baseline-from <optimize-run-id>` or `--baseline-defaults`); the workflow is not invoked

#### Scenario: No API key set

- **WHEN** the operator runs the command without `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` set in the environment
- **THEN** the CLI exits non-zero with a stderr message naming the required env vars

#### Scenario: Engine worker binary missing

- **WHEN** the engine-worker binary is not built at the configured path
- **THEN** the CLI exits non-zero with a stderr message naming the binary path and suggesting `cd crates && cargo build -p engine-worker`

### Requirement: Evaluate-fold factory supports experiment.yaml and smoke fallback

The CLI wiring SHALL construct an `EvaluateFoldFn` from the strategy crate's bars source. When `crates/<name>-strategy/experiment.yaml` exists, the factory MUST read the fold scheme from it and dispatch one engine batch per fold. When `experiment.yaml` is absent, the factory MUST fall back to a single-fold evaluator built from `smoke.toml`: one engine submission over the smoke window, results returned as fold 0. Both modes MUST honor the dataset-manifest hash captured during the fetch so the workflow's reproducibility contract holds.

#### Scenario: Multi-fold dispatch via experiment.yaml

- **WHEN** the strategy crate has an `experiment.yaml` declaring three walk-forward folds and the CLI builds the evaluate-fold factory
- **THEN** invoking the returned callable with `(params, fold_idx=0..2)` submits one engine batch per fold and returns the metrics dict each fold produced

#### Scenario: Single-fold fallback from smoke.toml

- **WHEN** the strategy crate has no `experiment.yaml` and the CLI builds the evaluate-fold factory
- **THEN** the factory returns a callable that accepts `fold_idx=0` only, submits one batch over the smoke window, and the workflow's mechanical gate falls back to its n=1 fixed-margin threshold (existing behavior; this scenario documents that the fallback path is reached, not a new gate behavior)

### Requirement: Baseline resolution has two explicit modes

The CLI SHALL resolve the baseline `BacktestResult` + per-fold scores + metrics + files + params tuple from one of two sources, selected by mutually-exclusive flags:

- `--baseline-from <optimize-run-id>` reads `best.json` and per-fold artifacts from the optimization ledger row and lifts them into the `HypothesizeDeps` baseline fields.
- `--baseline-defaults` invokes the same `EvaluateFoldFn` the loop will use, with the crate's default parameter values (parsed from `intent.toml.param_schema_sketch`), to produce a fresh baseline in the same metric space as the candidates.

The two flags MUST NOT both be set; setting neither and finding no optimize run MUST be a typer error (see *CLI constructs HypothesizeDeps end-to-end*).

The CLI MUST surface which source was used in the result envelope's `baseline_source` field so operators can read the comparison context downstream.

#### Scenario: `--baseline-from` loads from optimize ledger

- **WHEN** the operator passes `--baseline-from opt-2026-05-20-spy-atr` and the optimize-run row exists with a `best.json` and per-fold artifacts
- **THEN** the wiring loads the `BacktestResult`, per-fold scores, metrics, files, and params from the ledger artifacts and the result envelope's `baseline_source` is `"optimize_run:opt-2026-05-20-spy-atr"`

#### Scenario: `--baseline-defaults` computes from defaults

- **WHEN** the operator passes `--baseline-defaults` and `intent.toml.param_schema_sketch` carries each param's `default`
- **THEN** the wiring invokes the evaluate-fold factory at the default params for every configured fold, assembles the `BacktestResult` + per-fold scores + metrics from the results, and the result envelope's `baseline_source` is `"baseline_defaults"`

#### Scenario: Both flags set is a typer error

- **WHEN** the operator passes both `--baseline-from` and `--baseline-defaults`
- **THEN** the CLI exits with a typer.BadParameter error naming the mutual exclusion (existing behavior; this scenario documents that the new wiring preserves it)

### Requirement: KB store path defaults to `kb/store/` with lazy build

The CLI wiring SHALL bind the KB client to a SQLite-resident hybrid-retrieval store. The store path resolves from `--kb-store <path>`, then `KB_STORE_PATH` env var, then the default `kb/store/`. When the resolved path has no store on disk, the wiring MUST invoke the existing ingestion path against `kb/sources.toml` to build it before constructing the client; subsequent runs reuse the persisted store. A `--rebuild-kb` flag MUST force rebuilding regardless of whether the store exists.

#### Scenario: First-run ingests, subsequent runs reuse

- **WHEN** the resolved KB store path has no store on disk and the CLI is invoked
- **THEN** the wiring ingests `kb/sources.toml` into the path, prints a one-time progress banner, persists the resulting store, and constructs the `KbClient` from it; a second invocation with the same path reuses the store without re-ingesting

#### Scenario: --rebuild-kb forces a fresh build

- **WHEN** the operator passes `--rebuild-kb` and the store already exists
- **THEN** the wiring rebuilds the store from `kb/sources.toml` before constructing the client

### Requirement: Stage models default per-stage and are overridable

The CLI SHALL construct the `StageReasoningClient` via the existing `reasoning_clients._StageRouter`, with per-stage model defaults resolved from the environment (existing helper). The CLI MUST expose per-stage override flags `--model-stage1`, `--model-stage2`, `--model-stage3`, `--model-critique`, `--model-rank`; each flag overrides only the named stage, leaving the others at their defaults.

#### Scenario: Default models are used when no override is set

- **WHEN** the operator runs hypothesize without any `--model-*` flag
- **THEN** every stage gets its environment-resolved default model

#### Scenario: Per-stage override is scoped to the named stage

- **WHEN** the operator passes `--model-stage1 claude-opus-4-7`
- **THEN** only stage 1 uses `claude-opus-4-7`; stages 2, 3, critique, and rank keep their defaults

