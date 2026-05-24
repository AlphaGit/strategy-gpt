# Run the hypothesize loop

Task-oriented recipe for invoking the strategy-logic search loop on an
authored strategy crate.

## Prerequisites

- The strategy has been **authored** (`strategy-gpt author <name>`). The
  crate must carry `intent.toml` + `smoke.toml`; an optional
  `experiment.yaml` (from `author --verify=batch`) unlocks multi-fold
  evaluation.
- The **engine-worker binary is built**: `cd crates && cargo build -p engine-worker`. Defaults to `crates/target/debug/engine-worker`; override with `--engine-worker`.
- `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` is set in the environment.
- A baseline source. Two modes:
  - `--baseline-defaults`: smoke-runs the crate at `intent.toml.param_schema_sketch` defaults. Cheap path; the comparison space matches the candidates but the baseline is less rigorous than an optimized one.
  - `--baseline-from <opt_run_id>`: lifts `best.json` + per-fold OOS metrics from `ledger/optimizations/<opt_run_id>/`. Use after a real optimize run.
- A per-strategy ledger root (defaults to `./ledger`); created on demand.

## CLI surface

```bash
strategy-gpt hypothesize <strategy> \
    --baseline-defaults                 # OR --baseline-from <opt-run-id>
    [--objective sharpe] \
    [--engine-worker crates/target/debug/engine-worker] \
    [--cache-root cache/builds] [--work-root cache/build-work] \
    [--gateway-root cache] [--crates-dir crates] \
    [--kb-store kb/store] [--rebuild-kb] \
    [--model-stage1 ...] [--model-stage2 ...] [--model-stage3 ...] \
    [--model-critique ...] [--model-rank ...] \
    [--max-backtests N] [--k-candidates 3] [--iteration-budget 4] \
    [--quick] [--borderline-k 1.0] [--llm-critic] \
    [--quiet] [--dry-run]
```

The command builds `HypothesizeDeps` end-to-end from the crate at
`crates/<strategy>-strategy/` and the operator's environment, invokes the
workflow, and prints a JSON envelope on stdout mirroring `HypothesizeResult`
plus a `baseline_source` label. Per-stage progress streams to **stderr**
so the stdout JSON stays pipeable.

### Flag reference

| Flag | Effect |
|------|--------|
| `--baseline-defaults` | Smoke-run defaults as the baseline (mutex with `--baseline-from`). |
| `--baseline-from <id>` | Load baseline from an optimize-run row. |
| `--objective <metric>` | Objective metric (default `sharpe`). |
| `--engine-worker <path>` | Engine-worker binary path. |
| `--cache-root <dir>` / `--work-root <dir>` | Build-pipeline cache + scratch. |
| `--gateway-root <dir>` | Gateway cache root for bars. |
| `--crates-dir <dir>` | Workspace crates dir (default `crates`). |
| `--kb-store <dir>` | KB store path; default `kb/store/`. First run ingests `kb/sources.toml` lazily with a one-time progress banner. |
| `--rebuild-kb` | Force-rebuild the KB store. |
| `--model-stage{1,2,3,critique,rank} <id>` | Per-stage reasoning model overrides. Defaults to the env-resolved most-capable model per stage. |
| `--max-backtests N` | Hard ceiling on cumulative backtests. |
| `--quick` | Single-fold evaluator; small mini-optimize budget. |
| `--k-candidates N` | Target accepted-candidate count (default 3). |
| `--iteration-budget N` | Inner-loop iteration cap (default 4). |
| `--borderline-k <float>` | Mechanical-gate `k` coefficient (accepted but not yet propagated to workflow — see Known limitations). |
| `--llm-critic` | Opt into the LLM verdict critic (currently warns + falls back to the deterministic critic). |
| `--quiet` | Suppress per-node + per-attempt progress on stderr. |
| `--dry-run` | Validate inputs, print the resolved dep summary, exit without invoking the workflow. |

### Pre-workflow validation gates

Each gate exits with `exit_code=2` + a stderr message naming the missing
artifact and the next step. No partial run; nothing persisted.

| Gate | Failure |
|------|---------|
| Crate dir exists | `crates/<name>-strategy/ does not exist; run 'strategy-gpt author <name>' first` |
| `intent.toml` / `smoke.toml` present | Names the missing file |
| API key set | `set ANTHROPIC_API_KEY or OPENAI_API_KEY before running hypothesize` |
| Baseline flag supplied | `no baseline provided; pass --baseline-from <optimize-run-id> or --baseline-defaults` |
| Engine-worker binary present | Names the path + suggests `cd crates && cargo build -p engine-worker` |
| `PROMPT_API.md` readable | Names the path |
| Optimize-run exists (when `--baseline-from`) | Names the missing run id |

## Progress output

With `--quiet` off (default), stderr carries human-readable phase
transitions while the loop runs. Sample:

```
[hypothesize] strategy=spy_atr baseline=baseline_defaults folds=3 objective=sharpe budget(iter=4,backtests=unbounded)
• diagnose: baseline sharpe=0.612 return=8.20% max_dd=18.00% trades=42
    weakest regime: high_vol (sharpe=0.103)
    signal misfires: rsi_oversold, atr_spike
• kb_query: 5 citation(s) (mertens_2008, bouchaud_2018, ang_2014)
• kb_filter: 3 citation(s) after prior-decision filter
    > baseline_defaults: running fold 1/3 with defaults={'atr_window': 14, ...}...
    > baseline_defaults: fold 1/3 done sharpe=0.6121 (sharpe=0.6121, trades=42, max_dd=18.00%, ann_ret=8.20%)
━━━ iteration 1/4 ━━━
  ✓ stage1 idea: tighten_vol_lo (confidence=70%)
    why: Lower vol_lo entry threshold from 0.012 to 0.008 ...
  ✓ cheap_critique passed (idea worth committing)
    > stage2: requesting LLM (initial, attempt 1/3)
    > stage2: LLM emission received (608 chars in 5.6s)
    > stage2: validating done in 0.0s (ok)
  ✓ stage2 commitments: must beat baseline sharpe by gt +0.1000 (1 guard(s))
    > stage3: requesting LLM (initial, attempt 1/3)
    > stage3: LLM emission received (13104 chars in 25.1s)
    > stage3: compiling + linting emission...
    > stage3: compiling + linting done in 41.8s (ok)
  ✓ stage3 built strategy crate (3 files: Cargo.toml, params_schema.json, src/lib.rs)
    > mini_optimize trial 1/192 fold 0: sharpe=0.4510 best=0.4510 (baseline=0.6121, delta=-0.1611)
    > mini_optimize trial 2/192 fold 0: sharpe=0.8320 ↑ best=0.8320 (baseline=0.6121, delta=+0.2199)
    ... (running best updates only as score improves)
  ✓ mini_optimize ↑ candidate=1.2583 vs baseline=1.0167 (+23.8% on objective; backtests_consumed=192)
  ✓ accept mechanical_gate: delta=+0.2416 vs floor 1.0*sigma=0.0223 (fold_cv=0.022/0.5)
  ✓ accept verdict_critique
  • rank: 1 accepted [tighten_vol_lo], 0 rejected so far
━━━ loop complete ━━━
  ✓ accepted: tighten_vol_lo (target=sharpe) — clean win, no side-effect envelope breaches
  termination: sufficient_candidates
```

Heartbeats inside `_emit_with_repair` (`> stage{N}: requesting LLM...`)
and inside `_instrument_evaluator` (`> mini_optimize trial K/M ...`)
report long-running phases — LLM requests and engine subprocess
batches each take seconds-to-minutes; without these the loop appears
stalled.

## How the loop runs candidates

1. **`diagnose`** the baseline → identify weakest regime, misfiring signals.
2. **KB retrieval** + **prior-decision filter**.
3. Inner loop (up to `--iteration-budget`):
   - **stage 1 (idea)** → **cheap_critique** → **stage 2 (commitments)** → **stage 3 (file emission + cargo build)**.
   - Each stage runs through a bounded repair loop. Repair retries feed the LLM the previous emission **verbatim** plus the validator's error (rustc output, lint reasons, schema mismatch), so it can patch in place rather than re-emit blind.
   - **mini_optimize** runs the **candidate's** freshly-built shared library (not the baseline's) across folds × Sobol trials; per-trial running-best heartbeats are emitted.
   - **mechanical_gate** applies the variance-aware floor (`k · σ_combined`) + per-fold CV.
   - **verdict_critique** (deterministic; LLM variant is a follow-up).
   - **rank**.
4. **`select`** trims to `--k-candidates` and reports `termination_reason`.

## Decision taxonomy

The per-strategy ledger persists each candidate with one of three outcomes:

| Outcome | Trigger | Affects future ideation? |
|---------|---------|--------------------------|
| `accepted` | Workflow accepted | Yes (boosts similar ideas) |
| `rejected` | Logic-level failure (`reject_schema`, `reject_smoke`, `reject_noise`, `reject_variance`, `reject_verdict`) | Yes (biases against similar ideas via `cheap_critique` duplicate-similarity check) |
| `deferred` | **Mechanical** failure: `reject_build`, `reject_lint`, `reject_format`, `reject_deps`, `exhausted_repair_budget` on stages 1–3 | **No** — the LLM couldn't *compile* the code, but the underlying hypothesis is preserved. Future runs may re-propose the same idea with cleaner code. |

The CLI progress renderer surfaces deferred counts distinctly:
`• rank: 0 accepted, 1 rejected, 2 deferred so far`.

## Baseline modes — semantic details

- `--baseline-defaults` builds the baseline by invoking the same
  `evaluate_fold` the loop will use, at the parameter defaults declared
  in `intent.toml.param_schema_sketch`. Per-fold heartbeats stream
  during baseline computation so the operator sees the metrics before
  candidates start.
- `--baseline-from <opt-run-id>` reads `ledger/optimizations/<id>/best.json`
  plus per-fold `oos_metrics`. Synthesises a minimal `BacktestResult`
  from the aggregate metrics (the optimize ledger does not persist full
  payloads); diagnosis-quality is bounded by what's present.

The result envelope's `baseline_source` field labels which path was used: `"baseline_defaults"` or `"optimize_run:<id>"`.

## Per-candidate library binding

Stage-3 compiles each candidate to its own shared library via the
build pipeline. The orchestrator routes mini-optimize through the
**candidate's** library, not the baseline's, so per-trial backtests
actually exercise the new strategy code. (Earlier wiring closure-baked
the baseline path into the evaluator and every candidate scored
identically — that bug is fixed.)

## Known limitations

- `--llm-critic` is accepted but currently falls back to the
  deterministic critic with a stderr warning. The LLM critic surface
  is a tracked follow-up.
- `--borderline-k` is accepted but does not propagate to the workflow
  yet (the underlying `HypothesisLoopConfig` is frozen + slotted; a
  schema bump is needed). The mechanical gate runs with the default
  `k=1.0` regardless.
- Reasoning-only OpenAI models (`o1*`, `o3*`, `o4*`, `gpt-5*`) reject
  any custom `temperature` and consume reasoning tokens from
  `max_completion_tokens`. The reasoning client gates `temperature`
  off for those families and floors `max_completion_tokens` at 32 k so
  stage-3 has room to emit a multi-file Rust bundle after reasoning.

## Python entry (full wiring)

The orchestrator entry runs the full workflow end-to-end with caller-supplied
collaborators:

```python
from pathlib import Path
from strategy_gpt.hypothesize import HypothesizeDeps, hypothesize
from strategy_gpt.per_strategy_ledger import PerStrategyLedger
from strategy_gpt.reasoning import HypothesisLoopConfig

ledger = PerStrategyLedger(Path("ledger"), "vxx_volatility_range")
deps = HypothesizeDeps(
    kb=my_kb_client,                         # implements `KbClient`
    stage_client=my_stage_client,            # implements `StageReasoningClient`
    build_pipeline=my_build_pipeline,
    evaluate_fold=my_engine_evaluator,       # baseline-bound; (params, fold_idx) -> BacktestMetrics
    evaluate_fold_factory=my_factory,        # (library_path) -> EvaluateFoldFn  — see below
    prompt_api=Path("crates/engine-rt/PROMPT_API.md").read_text(),
    allowed_metrics=["sharpe", "sortino", "max_drawdown", "n_trades", ...],
    baseline_result=baseline_backtest_result,
    baseline_files=baseline_source_bundle,
    baseline_params_schema=baseline_params,
    baseline_per_fold_scores=[0.95, 1.05, 1.10],
    baseline_metrics={"max_drawdown": 0.08, "n_trades": 120, ...},
    baseline_aggregate_score=1.03,
    objective_metric="sharpe",
    dataset_manifest_hash=manifest_hash,
)
result = hypothesize(
    "vxx_volatility_range",
    ledger=ledger,
    deps=deps,
    config=HypothesisLoopConfig.with_defaults(
        target_candidates=3,
        iteration_budget=4,
    ),
    persist=True,
    max_backtests=2000,
    progress=my_progress_callback,        # optional (node_name, delta, state) -> None
    attempt_sink=lambda msg: print(msg),  # optional per-LLM-attempt + per-trial heartbeat
)
print(result.termination_reason, len(result.accepted))
```

`evaluate_fold_factory` is what enables per-candidate library binding:
when set, the workflow's `mini_optimize_step` calls
`factory(candidate_library_path)` to build an evaluator bound to the
candidate's freshly-compiled `.so` / `.dylib`. When unset, the workflow
falls back to `evaluate_fold` (the baseline-bound closure) — useful
for offline smoke tests with a stub evaluator.

## Python entry (full wiring)

The orchestrator entry runs the full workflow end-to-end with caller-supplied
collaborators:

```python
from pathlib import Path
from strategy_gpt.hypothesize import HypothesizeDeps, hypothesize
from strategy_gpt.per_strategy_ledger import PerStrategyLedger
from strategy_gpt.reasoning import HypothesisLoopConfig, ReasoningModel

ledger = PerStrategyLedger(Path("ledger"), "vxx_volatility_range")
deps = HypothesizeDeps(
    kb=my_kb_client,                         # implements `KbClient`
    stage_client=my_stage_client,            # implements `StageReasoningClient`
    build_pipeline=my_build_pipeline,        # implements the trusted build surface
    evaluate_fold=my_engine_evaluator,       # (params, fold_idx) -> BacktestMetrics
    prompt_api=Path("crates/engine-rt/PROMPT_API.md").read_text(),
    allowed_metrics=["sharpe", "sortino", "max_drawdown", "n_trades", ...],
    baseline_result=baseline_backtest_result,
    baseline_files=baseline_source_bundle,
    baseline_params_schema=baseline_params,
    baseline_per_fold_scores=[0.95, 1.05, 1.10],
    baseline_metrics={"max_drawdown": 0.08, "n_trades": 120, ...},
    baseline_aggregate_score=1.03,
    objective_metric="sharpe",
    dataset_manifest_hash=manifest_hash,
)
result = hypothesize(
    "vxx_volatility_range",
    ledger=ledger,
    deps=deps,
    config=HypothesisLoopConfig.with_defaults(
        target_candidates=3,
        iteration_budget=4,
    ),
    persist=True,
    max_backtests=2000,
)
print(result.termination_reason, len(result.accepted))
```

## What lands on disk

For each accepted/rejected candidate the orchestrator writes:

- A row in `ledger/strategies/<strategy>/hypothesis_records.parquet`
- A row in `ledger/strategies/<strategy>/decision_records.parquet`
- A content-addressed source bundle under `sources/<files_set_hash>/`
- Stage-1/2/3 markdown blobs under `responses/<decision_id>/`

Replay and diff commands consume these:

```bash
strategy-gpt hypothesis replay <decision_id> --ledger-root ledger
strategy-gpt hypothesis diff   <decision_id> --ledger-root ledger
```

## Reading the result

A typical accepted candidate carries:

- `aggregate_score` and `per_fold_best_scores` from the mini-optimize pass
- `falsification_check.classification` — one of `accepted` / `falsified` /
  `regression` (mechanical-gate noise/variance rejections appear under
  `rejected` instead)
- `side_effect_flags` — metric deltas vs baseline that crossed a tolerance band

A `regression` classification means the primary claim was met but a guard
constraint was broken (e.g. drawdown rose past the stated envelope); these are
rejected regardless of primary outcome.

## Troubleshooting

- **`reject_format` / `reject_lint` storms** — the LLM is emitting
  malformed stage-3 files. Inspect
  `responses/<decision_id>/stage3_files.md`; the strict parser's
  section identifier appears in the rejection rationale, and the
  `_emit_with_repair` retries now feed the LLM the previous emission +
  verbatim error so it can patch in place rather than re-emit blind.
  Persistent failures persist as **deferred** decisions and do NOT bias
  future ideation.
- **`reject_build` storms** — same recipe as above; rustc errors land
  in the rejection rationale and in the repair feedback. The candidate
  is recorded as **deferred** (mechanical), so a fresh stage-3 attempt
  on the same idea is allowed in subsequent runs.
- **`reject_deps`** — candidate's Cargo.toml declared a crate outside
  the whitelist. Either widen `crates/build-pipeline/whitelist.toml`
  (intentional change) or improve the stage-1/2 prompts. Persists as
  deferred.
- **`reject_schema`** — stage-2 named a `kept` parameter that does not
  exist on the baseline strategy schema. The stage-2 validator now
  catches this and feeds the LLM the allowed-names list so the next
  attempt fixes it. Mini-optimize also wraps its search-space builder
  in `try/except (ValueError, TypeError, KeyError)` to surface
  malformed `param_intent` as `reject_schema` instead of crashing the
  loop.
- **All candidates show identical scores (`±0.0` deltas)** — was a
  bug; the evaluator closure had the baseline library path baked in.
  Fixed via `evaluate_fold_factory` per-candidate binding. If it
  recurs, check that `validate_stage3` returned a non-None
  `build_outcome.artifact.library_path` and that
  `state["candidate_library_path"]` is set after stage 3.
- **`reject_noise`** — candidate beats the baseline but not by
  `k · σ_combined`. Tune `--borderline-k` lower (less stringent) once
  that flag is plumbed; today it is accepted but unused — the gate
  runs with `k=1.0`.
- **Budget exhausted** — increase `--max-backtests`, lower mini-optimize trials via `--quick`, or reduce `--k-candidates`.
- **OpenAI 400: `temperature does not support 0.7`** — fixed; reasoning-only models (`o1*`/`o3*`/`o4*`/`gpt-5*`) now have `temperature` omitted from the request.
- **OpenAI stage-3 response content was empty** — usually means reasoning tokens consumed the whole `max_completion_tokens` budget. The client floors it at 32 k for reasoning models; if you still see this, the empty-content error now includes `finish_reason` + `reasoning_tokens` for diagnosis.
- **CI / `maturin develop` fails with `Couldn't find a virtualenv`** — fixed in `.github/workflows/ci.yml`; deps install into `.venv` and `VIRTUAL_ENV` is exported for all subsequent steps.
