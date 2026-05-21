# `strategy-gpt hypothesize` reference

Exhaustive CLI surface for the hypothesis-loop entry and its companion
replay/diff commands.

## `strategy-gpt hypothesize <strategy>`

Drive the multi-stage logic-search loop on a strategy crate.

### Arguments

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `strategy` | `str` | yes | Strategy crate name (e.g. `vxx_volatility_range`). Used as the per-strategy ledger subfolder name. |

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--ledger-root` | `Path` | `ledger` | Root of the per-strategy ledger. The strategy's history lives under `<ledger-root>/strategies/<strategy>/`. |
| `--baseline-from` | `str` | — | Optimization run id to load the baseline-best from. Mutually exclusive with `--baseline-defaults`. |
| `--baseline-defaults` | `bool` | `false` | Use baseline-defaults (no optimize) for the baseline. Mutually exclusive with `--baseline-from`. |
| `--max-backtests` | `int` | — | Hard ceiling on cumulative backtests across the run. Iterations that would exceed this exit with `termination_reason = budget_exhausted`. |
| `--quick` | `bool` | `false` | Quick mode: small mini-optimize budget (16 trials per candidate). |
| `--borderline-k` | `float` | `1.0` | Mechanical-gate score-floor coefficient (`Δ > k · σ_combined`). |
| `--k-candidates` | `int` | `3` | Target accepted-candidate count per run. |
| `--iteration-budget` | `int` | `4` | Inner-loop iteration cap. |
| `--dry-run` | `bool` | `false` | Validate inputs, print resolved flags as JSON, and exit. |

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Successful run (terminated for any reason); accepted/rejected lists printed to stdout. |
| `2` | Bad CLI input (`typer.BadParameter`): e.g. both `--baseline-from` and `--baseline-defaults` supplied. |
| nonzero | Workflow exception (e.g. ledger I/O failure, build-pipeline crash). |

### Output (JSON to stdout)

```json
{
  "strategy": "vxx_volatility_range",
  "termination_reason": "sufficient_candidates",
  "iterations": 2,
  "backtests_consumed": 384,
  "n_accepted": 3,
  "n_rejected": 1,
  "accepted": [
    {"name": "tighten_vol_lo", "rationale": "..."},
    {"name": "add_treasury_hedge", "rationale": "..."},
    {"name": "widen_vol_hi", "rationale": "..."}
  ],
  "rejected": [
    {"name": "rewrite_entry_logic", "reason": "reject_format: ..."}
  ],
  "persisted_decision_ids": ["...", "...", "...", "..."]
}
```

`termination_reason` is one of `sufficient_candidates`, `budget_exhausted`,
`similarity_saturation`, `running` (only on early exits).

## `strategy-gpt hypothesis replay <decision_id>`

Reconstruct a recorded candidate from the per-strategy ledger.

### Arguments

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `decision_id` | `str` | yes | `DecisionRecord` id to replay. |

### Options

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--ledger-root` | `Path` | `ledger` | Per-strategy ledger root. |
| `--strategy` | `str` | — | Restrict the lookup to one strategy. Omit to scan every strategy under `<ledger-root>/strategies/`. |

### Output (JSON)

```json
{
  "strategy": "vxx_volatility_range",
  "decision_id": "abcd...",
  "hypothesis_id": "efgh...",
  "candidate_name": "tighten_vol_lo",
  "outcome": "accepted",
  "stage": null,
  "files_set_hash": "blake2:...",
  "files_in_bundle": ["Cargo.toml", "params_schema.json", "src/lib.rs"],
  "n_files": 3,
  "param_intent_added": ["vol_lo"],
  "param_intent_kept": [],
  "param_intent_removed": [],
  "falsification_primary": {
    "metric": "sharpe",
    "direction": "gt",
    "delta_vs_baseline": 0.1,
    "scope": {"kind": "aggregate"}
  }
}
```

Operators typically pipe this into a Python script that re-runs the
mini-optimize against the recorded source bundle.

## `strategy-gpt hypothesis diff <decision_id>`

Render a unified diff between the candidate source bundle and the
baseline bundle recorded on the same hypothesis.

### Arguments / Options

Identical to `hypothesis replay`.

### Output

Unified diff to stdout, one block per file:

```
# strategy=vxx_volatility_range decision_id=abcd... candidate=blake2:1234 baseline=blake2:5678
--- baseline/src/lib.rs
+++ candidate/src/lib.rs
@@ -1,4 +1,5 @@
 // existing logic
+// new hedge leg
```

Files present in only one bundle are reported as full-file add/delete diffs.

## Environment variables

| Variable | Effect |
|----------|--------|
| `ANTHROPIC_API_KEY` | Selects an Anthropic reasoning model (Opus → Sonnet ranking). |
| `OPENAI_API_KEY` | Selects an OpenAI reasoning model (`o3` → `o1`). |
| `RUSTC_WRAPPER` | When set to `sccache`, build-pipeline candidate builds use sccache. |

Without an API key the workflow startup raises
`NoReasoningModelAvailableError`; pass an explicit `ReasoningModel`
override from the Python entry to bypass.
