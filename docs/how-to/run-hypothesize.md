# Run the hypothesize loop

Task-oriented recipe for invoking the strategy-logic search loop on a strategy crate.

## Prerequisites

- A strategy crate that compiles under the build-pipeline whitelist (the reference
  is `crates/vxx-strategy/`).
- A baseline `BacktestResult` for that strategy. Most operators get this from the
  most recent `optimize` run; callers driving the Python API supply it directly.
- A per-strategy ledger root (defaults to `./ledger`). First-time runs create
  `ledger/strategies/<strategy>/` on demand.
- `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` set in the environment — the workflow
  resolves the most capable reasoning model at startup.

## CLI surface

```bash
strategy-gpt hypothesize <strategy> \
    --ledger-root ledger \
    --max-backtests 2000 \
    --k-candidates 3 \
    --iteration-budget 4 \
    [--baseline-from <opt_run_id>] [--baseline-defaults] \
    [--quick] [--borderline-k 1.0] [--dry-run]
```

The command builds the operator-specific collaborators (KB, build pipeline,
stage reasoning client, engine evaluator, baseline tuple) from the crate at
`crates/<strategy>-strategy/` and the operator's environment, invokes the
workflow, and prints a JSON envelope mirroring `HypothesizeResult` (plus the
`baseline_source` label). The Python entry below is the same surface for
programmatic callers.

Flags:

| Flag | Effect |
|------|--------|
| `--max-backtests N` | Hard ceiling on cumulative backtests. Rejects an iteration whose mini-optimize would exceed the budget. |
| `--quick` | Use a small mini-optimize budget (16 trials) — useful for early iteration on a new strategy. |
| `--borderline-k <float>` | Mechanical-gate `k` coefficient. Default `1.0` (~68% Gaussian confidence). |
| `--k-candidates N` | Target accepted-candidate count per run (default `3`). |
| `--iteration-budget N` | Inner-loop iteration cap (default `4`). |
| `--baseline-from <id>` | Load the baseline-best from a recorded optimize run id. |
| `--baseline-defaults` | Use baseline-defaults (no optimize). Mutually exclusive with `--baseline-from`. |
| `--dry-run` | Validate inputs, print resolved flags, exit without invoking the workflow. |

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

- **`reject_format` / `reject_lint` storms** — the LLM is emitting malformed
  stage-3 files. Inspect `responses/<decision_id>/stage3_files.md`; the strict
  parser's section identifier appears in the rejection rationale.
- **`reject_deps`** — the candidate's Cargo.toml declared a crate outside the
  allowed-crate whitelist. Repair attempts re-prompt with a "remove this dep"
  message; if repeated, widen the whitelist in `build-pipeline` (intentional
  change) or improve the stage-1/2 prompts.
- **`reject_noise`** — candidate beats the baseline but not by `k · σ_combined`.
  Either tune `--borderline-k` lower (less stringent) or accept that the
  candidate failed the variance-aware floor.
- **Budget exhausted** — increase `--max-backtests`, lower mini-optimize trials
  via `--quick`, or reduce `--k-candidates`.
