# Walking the hypothesize loop

## Learning goal

Drive the hypothesis-loop CLI surface (`strategy-gpt hypothesize`,
`strategy-gpt hypothesis replay`, `strategy-gpt hypothesis diff`) end
to end against an in-repo fixture ledger — without setting an
`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`. By the end you have a
per-strategy ledger on disk, a recorded `decision_id` you can replay,
and a unified diff showing the candidate's source bundle against the
baseline.

## Prerequisites

- You have completed [Your first backtest](first-backtest.md) — the
  Python package is installed in editable mode and the `strategy-gpt`
  console script is on `$PATH`.
- No LLM API keys required. The fixture ledger is generated from a
  fully stubbed smoke driver (`python/strategy_gpt/smoke.py`) — no
  network calls, no real builds.

## Walkthrough

### 1. Generate a fixture per-strategy ledger

The smoke driver exercises the full hypothesis-loop workflow with
pre-baked KB, stage-emission, and build-pipeline stubs. Pass
`--ledger-root` to keep the recorded rows + source bundles on disk
(the default is an ephemeral tempdir for the in-process regression
test):

```bash
python -m strategy_gpt.smoke --ledger-root ledger
```

Expected (truncated):

```json
{
  "accepted_aggregate_scores": [
    1.258256
  ],
  "accepted_names": [
    "tighten_vol_lo"
  ],
  "backtests_consumed": 192,
  "baseline_aggregate_score": 1.016667,
  "iterations": 2,
  "kb_citation_count": 2,
  "persisted_decision_count": 2,
  "rejected_names": [
    "widen_vol_hi"
  ],
  "strategy": "vxx_volatility_range",
  "termination_reason": "budget_exhausted"
}
```

The driver records under the strategy slug `vxx_volatility_range`.
The two persisted decisions are one accepted candidate
(`tighten_vol_lo`) plus one rejected candidate (`widen_vol_hi`); both
end up in the per-strategy ledger so the replay/diff steps below have
something to read.

### 2. Smoke-test the hypothesize CLI with `--dry-run`

`strategy-gpt hypothesize <strategy> --dry-run` builds none of the
collaborators and instead echoes the resolved deps summary (baseline
source, fold source, per-stage models, engine-worker path, budgets).
Useful for confirming flag parsing without spending tokens:

```bash
strategy-gpt hypothesize vxx_volatility_range --baseline-defaults --dry-run
```

Expected (truncated):

```json
{
  "dry_run": true,
  "strategy": "vxx_volatility_range",
  "baseline_source": "baseline_defaults",
  "objective_metric": "sharpe",
  "fold_source": "smoke.toml (single fold)",
  "stage_models": {},
  "iteration_budget": 4,
  "k_candidates": 3
}
```

### 2a. Drive a real loop run end-to-end

With an `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`) set and the engine-worker built (`cd crates && cargo build -p engine-worker`), drop `--dry-run` to run the loop. The crate at `crates/vxx_volatility_range-strategy/` must have been authored cleanly (`intent.toml` + `smoke.toml` present); a freshly-authored crate works out of the box.

```bash
strategy-gpt hypothesize vxx_volatility_range --baseline-defaults --quick
```

The CLI builds `HypothesizeDeps` end-to-end, drives the workflow, prints a JSON envelope on stdout (`strategy`, `accepted`, `rejected`, `termination_reason`, `iterations`, `backtests_consumed`, `persisted_decision_ids`, `baseline_source`), and persists decisions under `ledger/strategies/vxx_volatility_range/`. See the how-to: [Run the hypothesize loop](../how-to/run-hypothesize.md) for the full flag surface.

### 2b. Fixture replay path (no LLM, no engine)

The smoke driver above is the offline replay path: the source bundles and decision rows it produced under `ledger/strategies/vxx_volatility_range/` exercise the CLI's `replay` and `diff` surfaces below without any LLM or engine collaborator wiring. Use this path when you want to walk the surfaces without spending tokens.

### 3. List the recorded decisions

The smoke driver wrote the per-strategy ledger under
`ledger/strategies/vxx_volatility_range/`. Each row in
`decision_records.parquet` is keyed by a `decision_id` (a `uuid4`
hex). The `responses/<decision_id>/` directories carry the stage
emissions and give you a quick visual confirmation:

```bash
ls ledger/strategies/vxx_volatility_range/responses/
```

Expected (your hashes will differ — `decision_id` is regenerated on
every smoke run):

```
4e2fa7f83937433c8f4dfede4d303878
415d75af910c496b835f8d5f80e53c37
```

Pick one of the two ids for the next two steps. The remaining
walkthrough uses `4e2fa7f83937433c8f4dfede4d303878` as a placeholder.

### 4. Replay a recorded candidate

`strategy-gpt hypothesis replay` reconstructs the candidate's
source-bundle summary from the per-strategy ledger and prints a JSON
payload that downstream replay tooling consumes (build + mini-
optimize). No LLM call. No native build.

```bash
strategy-gpt hypothesis replay 4e2fa7f83937433c8f4dfede4d303878
```

Expected:

```json
{
  "strategy": "vxx_volatility_range",
  "decision_id": "4e2fa7f83937433c8f4dfede4d303878",
  "hypothesis_id": "945c06292d1e442a9009598f43180320",
  "candidate_name": "tighten_vol_lo",
  "outcome": "accepted",
  "stage": null,
  "files_set_hash": "00c9cb72c95c38c3ca4680b97f30c578929b137207137c6a6298d4162c5bc43a",
  "files_in_bundle": [
    "Cargo.toml",
    "params_schema.json",
    "src/lib.rs"
  ],
  "n_files": 3,
  "param_intent_added": [
    "vol_lo"
  ],
  "param_intent_kept": [],
  "param_intent_removed": [],
  "falsification_primary": {
    "metric": "sharpe",
    "direction": "gt",
    "delta_vs_baseline": 0.1,
    "scope": {
      "kind": "aggregate",
      "regime": null,
      "fold": null,
      "window_start": null,
      "window_end": null
    }
  }
}
```

The `files_set_hash` is the content hash of the candidate's source
bundle under `ledger/strategies/vxx_volatility_range/sources/`.
`param_intent_added` is the LLM's contribution to the parameter
surface; `falsification_primary` is the falsifiable claim the
candidate was scored against.

### 5. Diff the candidate against the baseline

`strategy-gpt hypothesis diff` reconstructs both the candidate and
baseline source bundles and emits a per-file unified diff:

```bash
strategy-gpt hypothesis diff 4e2fa7f83937433c8f4dfede4d303878
```

Expected (truncated — your hashes will differ):

```
# strategy=vxx_volatility_range decision_id=4e2fa7f83937433c8f4dfede4d303878 candidate=00c9cb72c95c baseline=c29efd2c3d35
--- baseline/Cargo.toml
+++ candidate/Cargo.toml
@@ -1,3 +0,0 @@
-[package]
-name = "vxx_volatility_range"
-version = "0.1.0"
--- baseline/src/lib.rs
+++ candidate/src/lib.rs
@@ -1 +0,0 @@
-// baseline body (smoke)
```

The candidate source blobs persist as empty files for the smoke
driver (the workflow drops stage-3 response text after the iteration
closes; only the manifest of paths is recoverable for persist) so the
unified diff reads as a pure deletion of the baseline body. For a
real loop run the candidate bundle carries the LLM-emitted stage-3
files and the diff is a true line-by-line patch.

## What you just did

You touched all three commands in the hypothesis-loop CLI surface:
`strategy-gpt hypothesize ... --dry-run` (input-validation smoke
test against the bundled VXX strategy), `strategy-gpt hypothesis
replay <decision_id>` (load a recorded candidate from the per-
strategy ledger), and `strategy-gpt hypothesis diff <decision_id>`
(render a unified diff vs the baseline source bundle). The ledger
itself lives at `ledger/strategies/<strategy>/`, with
`hypothesis_records.parquet` + `decision_records.parquet` as the
append-only row stores and `sources/<files_set_hash>/` as the
content-addressed source-bundle store.

## What next

- **Tutorial** — [Running an optimization](running-an-optimization.md): sweep parameters across cross-validation folds and pick the OOS winner.
- **How-to** — [Run the hypothesize loop](../how-to/run-hypothesize.md):
  drive a full loop end-to-end via the Python entry
  (`hypothesize(...)`) with a real `HypothesizeDeps` payload.
- **Reference** — [hypothesize CLI](../reference/hypothesize-cli.md):
  every supported flag for `hypothesize`, `hypothesis replay`, and
  `hypothesis diff`.
- **Explanation** — [The hypothesize loop](../explanation/hypothesize-loop.md):
  the diagnose → kb_query → generate → critique → rank → select
  workflow shape and why each node exists.
- **Decision** — [ADR 0017 — Per-strategy storage
  layout](../decisions/0017-per-strategy-storage-layout.md): why the
  ledger lives under `ledger/strategies/<strategy>/` and how the
  source-bundle store is content-addressed.
