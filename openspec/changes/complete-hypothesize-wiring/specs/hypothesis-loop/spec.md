## ADDED Requirements

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
