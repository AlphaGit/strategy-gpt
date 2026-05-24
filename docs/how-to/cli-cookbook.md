# CLI cookbook

Common command lines for the workflows you'll run most often. The full subcommand reference is `strategy-gpt --help`; this page is the *recipes* layer above that.

> **Conventions.** Examples assume an activated venv (`source .venv/bin/activate`) and that `cargo build -p vxx-strategy` + `maturin develop` have already been run. Defaults: cache root `./cache`, ledger root `./ledger`. The reference strategy throughout is VXX; substitute your own symbol / artifact path freely.

---

## Datasets — download, reference, replay

### What is a dataset, in this system?

A **dataset** is a normalized OHLCV bar stream pinned to a content-addressed manifest. The data gateway (`crates/data-gateway`) produces one when you fetch from a provider. The manifest is the hash you'll see in `manifest_hash` on a successful fetch; it identifies the exact bytes the engine saw. The ledger stores the manifest alongside every run so any backtest can be reproduced byte-for-byte from `(ledger record + cache blobs)` without re-hitting the upstream provider.

Year segmentation: a single fetch over `2018-01-01 → 2026-12-31` produces *nine* cache blobs (one per calendar year). A later fetch over `2020-2024` reuses 2020-2024 blobs and only hits the network for the gap. Each `(provider, symbol, resolution, year, adjustment_policy)` tuple is its own cache key.

### Download VXX history

```bash
strategy-gpt fetch \
  --provider yfinance --symbol VXX \
  --start 2018-01-01 --end 2026-12-31 \
  --resolution Day --adjustment back_adjusted \
  --mode prefer_cache --root cache
```

Output:

```json
{
  "bar_count": 2087,
  "manifest_hash": "29bdecf5fe758d38d524025321aacfb2825daf2fbcce4a3c2c04377bf635b97b",
  "manifest_blobs": [ "<blob-hash>", ... ],
  "warning_count": 0
}
```

Save the `manifest_hash` — that's how every downstream surface (engine, ledger, optimizer) references this exact dataset.

### Cache modes — when to use which

| `--mode`        | Behavior |
|-----------------|----------|
| `prefer_cache`  | (default) Use cache when keys match; fetch only the missing years. |
| `validate`      | Re-fetch and diff against the cached blob; emit divergence warnings on disagreement. Currently aliased to `prefer_cache` (the full re-fetch/diff path is a planned follow-up). |
| `force_refresh` | Bypass cache entirely; refetch every year and overwrite blobs. |
| `offline`       | Never hit the network. Fail if any year is missing from the cache. Use for reproducibility-sensitive CI. |

### Inspect the cache

```bash
strategy-gpt cache-stats --root cache
# {"blob_count": 9, "total_bytes": 481234}
```

### Use a CSV provider for bring-your-own data

```bash
strategy-gpt fetch \
  --provider my_csv --symbol VXX \
  --csv-provider-dir ./my-csvs \
  --start 2018-01-01 --end 2024-12-31 \
  --resolution Day --adjustment back_adjusted
```

Expects `./my-csvs/VXX.csv` with header `timestamp,open,high,low,close,volume`. RFC 3339 or `YYYY-MM-DD` timestamps both accepted.

### Materialize cached bars to JSON

`strategy-gpt run` reads bars from a JSON file. Cache blobs are not directly that shape; pull them through the gateway:

```python
import json
from datetime import datetime, UTC
from pathlib import Path
from strategy_gpt.gateway import Gateway
from strategy_gpt.types import BarRequest, Resolution, AdjustmentPolicy

gw = Gateway('cache')
gw.register_yfinance_provider('yfinance')
req = BarRequest(
    provider='yfinance', symbol='VXX',
    start=datetime(2018,1,1,tzinfo=UTC), end=datetime(2026,12,31,tzinfo=UTC),
    resolution=Resolution.DAY, adjustment=AdjustmentPolicy.BACK_ADJUSTED,
)
resp = gw.fetch(req, 'prefer_cache')
bars = [b.model_dump(mode='json') for b in resp.bars]
Path('examples/vxx/bars.json').write_text(json.dumps(bars))
print(len(bars), 'bars')
```

This is a one-time per-dataset step. The output JSON is reused across every subsequent `strategy-gpt run` against the same window.

### Replay a recorded run

```bash
strategy-gpt replay --run-id <ledger-run-id> --ledger-root ledger --gateway-root cache
```

Reconstructs the `BatchSpec` + bars from the ledger and the cache. Identical inputs ⇒ byte-identical `BacktestResult`. This is the ledger's reproducibility guarantee in action; the upstream provider is never contacted.

### Inspect recent decisions

```bash
strategy-gpt recent-decisions --root ledger --limit 25
```

Returns accepted / rejected hypotheses with rationale, KB citations, and timestamps. The hypothesis loop re-loads this on its next run so it doesn't re-propose what's already been rejected.

---

## Strategies — build, run, interpret

### Build (one-time, after `cargo` or strategy edits)

```bash
cd crates && cargo build -p vxx-strategy -p example-strategy
cd crates && cargo build -p engine --bin engine-worker
```

Produces `crates/target/debug/libvxx_strategy.dylib` and `crates/target/debug/engine-worker`.

### Single-run backtest

```bash
strategy-gpt run \
  --spec examples/vxx/experiment.yaml \
  --worker crates/target/debug/engine-worker \
  --wait
```

| Flag | Purpose |
|---|---|
| `--spec`              | Path to an `experiment-spec` YAML or JSON. See [experiment-spec reference](../reference/experiment-spec.md). The spec carries `artifact`, `bars` (`dataset` or `request`), `engine`, `runs`, `parallelism`, and `caps`. |
| `--worker`            | `engine-worker` binary; the orchestrator spawns one subprocess per `RunSpec`. Defaults to `crates/target/debug/engine-worker`. |
| `--gateway-root`      | Gateway cache root used for bars resolution. Defaults to `cache`. |
| `--wait`              | Block until job completion; print full `JobStatus` JSON. Without it: print the handle and return immediately. |
| `--poll-interval-secs`| Poll interval when `--wait` is set. Default `0.5`. |

Per-run wall-clock and memory caps move into the spec under `caps:`
(`time_cap_secs`, `mem_cap_bytes`). Per-run slippage is no longer an
`engine` field; declare it as a `Slippage { bps_grid }` mode on the
affected run(s).

`JobStatus` shape returned by `--wait`:

```json
{ "status": "completed",
  "results": [
    { "status": "ok",
      "run_index": 0,
      "result": {
        "metrics":   { "sharpe": ..., "max_drawdown": ..., "n_trades": ..., ... },
        "trades":    [...],
        "signals":   [...],
        "equity":    [...],
        "regimes":   [...],
        "exec_log":  [...],
        "meta":      { "artifact_hash": "...", "dataset_manifest": "...", "seed": ..., "runner_version": "..." }
      }
    }
  ],
  "error": null
}
```

`status` is one of `completed | failed | cancelled`. On failure `error` is populated and `results` is null. See [BatchSpec JSON reference — `BacktestResult`](../reference/batch-spec.md) for the full output schema.

Each `results[i]` is a `RunResult` discriminated entry — successful runs carry `{ status: "ok", run_index, result }`; failed runs (only emitted under `failure_mode: continue`) carry `{ status: "failed", run_index, error_kind, message }`. `strategy-gpt run` defaults to `failure_mode: abort`, so a single bad run still surfaces as an outer `failed` job; opt into `continue` from a packed `BatchSpec` when you want per-run isolation (the optimizer's primary use case).

### Submit without waiting (manual polling)

```bash
HANDLE=$(strategy-gpt run --spec examples/vxx/experiment.yaml)
echo "submitted: $HANDLE"
# Poll later via the Python engine surface (`Engine.poll(handle)`); the CLI
# currently only exposes `--wait` for blocking polls.
```

### Tweak parameters without recompiling

Edit `examples/vxx/experiment.yaml`:

```yaml
runs:
  - params:
      vol_lo: 0.35    # change me
      vol_hi: 0.80    # change me
      size:   100.0
      symbol: VXX
```

Re-run `strategy-gpt run`. The artifact hash is unchanged; the engine reuses the compiled `.dylib`. Parameter changes never trigger a rebuild — that's what `params` is for.

### Multi-run sweep

Add more entries to the spec's `runs:` list. The engine compiles once and fans out across `parallelism` worker subprocesses. See [BatchSpec JSON reference — Multi-run example](../reference/batch-spec.md#multi-run-example-parameter-sweep) for the internal shape that `experiment-spec` translates into.

---

## Author a strategy

`strategy-gpt author` is the root primitive for creating a new strategy. It drives an interactive LLM dialog to elicit a structured intent, then emits, builds, and smoke-tests a working `cdylib` under `crates/<name>-strategy/`.

### Prerequisites

`ANTHROPIC_API_KEY` or `OPENAI_API_KEY` must be set in the environment. The workspace should be built once (`cd crates && cargo check --workspace`) and the Python orchestrator installed (`pip install -e 'python/[dev]'` + `maturin develop -m crates/py-bindings/Cargo.toml`).

### Invoke with a seed

```bash
strategy-gpt author "trend-follow SPY with ATR stops, daily bars"
```

The LLM opens with the seed and asks one focused clarifying question per turn until it has enough to commit to an `AuthorIntent`. Then it emits `Cargo.toml` + `src/lib.rs` + `smoke.toml` into `crates/<name>-strategy/`, runs `cargo build`, fetches bars, and runs a smoke backtest. On success the command prints a JSON envelope with the crate path and a next-step hint.

### Invoke with no seed

```bash
strategy-gpt author
```

The first dialog turn asks what you want to author. Everything else is the same.

### Edit an existing crate

Re-run `author` against the same name. The dialog detects the name collision, asks `edit` or rename, and on `edit` loads the existing `intent.toml`, `src/lib.rs`, `Cargo.toml`, and `smoke.toml` into context so subsequent emissions are framed as modifications. There is no `--edit` flag.

### Verify against the full walk-forward batch

```bash
strategy-gpt author "vol-target SPY" --verify=batch
```

After the smoke run, the engine runs the full batch declared in the emitted `experiment.yaml`. A failed fold pops control back to the dialog; the crate stays on disk for inspection.

### Tune the repair budget

| Flag | Default | Effect |
|------|---------|--------|
| `--k-repair-emit N`  | `2` | Repair attempts the emit/build/smoke stage gets. `k_repair=2` = 3 total attempts. |
| `--k-repair-build N` | `2` | Repair attempts the build sub-stage gets within an emit attempt. |
| `--model <name>`     | env-resolved | Override the reasoning model (e.g. `claude-sonnet-4-6`, `o3`). |
| `--crates-dir PATH`  | `crates` | Workspace crates directory. |
| `--cache-root PATH`  | `cache/builds` | Build pipeline cache root. |
| `--quiet`            | off | Suppress the locked-in decisions panel and collapse progress lines. |
| `--verbose`          | off | Stream per-line cargo / rustc output during build. |

### Give the dialog a multi-line answer

Short single-line answers work as you'd expect — type, press Enter. For
longer answers (a pasted YAML snippet, a multi-paragraph explanation,
a copied block of code) you have two options:

- **Paste it.** The CLI's input wrapper probes stdin after each line read
  and slurps any buffered lines that arrive together — so a multi-line
  paste lands as a single dialog turn. Just paste and press Enter once.
- **Type it with sentinels.** Type `<<<` on its own line to enter
  multi-line mode, then your content (any number of lines, blank lines
  preserved), then `>>>` on its own line to submit. The continuation
  prompt switches to `... ` while you're inside the block.

Both modes apply to the dialog turns and to the free-form guidance
prompts in the repair-exhaustion menu (option 1 "suggest an alternative
approach" and option 3 "edit a specific decision").

### Read the locked-in decisions panel

Between every dialog turn the CLI prints a banner-style panel showing
every clarification that has been locked in so far (crate name, universe,
mechanism summary, parameter sketch, smoke spec, …). The panel is
projected from `crates/<name>-strategy/.author/decisions.jsonl`, which is
the *authoritative* dialog state — the LLM's free-form chat history is
non-load-bearing, so even if the model compacts its working context the
panel keeps showing the truth. Pass `--quiet` to hide it.

### Watch progress events during emit/build/smoke

Default verbosity surfaces transitions only: `cargo build … done in 4.2s`,
`running smoke … ok (trades=3, sanity_trips=0)`, etc. Add `--verbose` to
get the underlying cargo argv and per-attempt result kinds. `--quiet`
collapses to the minimum (just the start-of-attempt and final result).

### When the repair loop gives up

When the emit/build/smoke loop burns through its repair budget, control
returns to the operator with a four-option menu:

| # | Option                          | Effect                                                                                          |
|---|---------------------------------|-------------------------------------------------------------------------------------------------|
| 1 | Suggest an alternative approach | Type a natural-language amendment. The LLM revises the intent and the loop restarts with fresh budget. |
| 2 | Retry with an extended budget   | Provide new `k_repair_emit` / `k_repair_build` values. Loop restarts with the same intent.        |
| 3 | Edit a specific decision        | Name a field (`mechanism_summary`, `param_sketch`, `smoke_spec`, `universe`) and the amendment. The LLM revises only that field. |
| 4 | Abort                           | Exit non-zero. The crate files and `.author/decisions.jsonl` stay on disk for inspection.        |

### Troubleshooting

- **`author run aborted: ... emit stage exhausted repair budget`** — the LLM could not get the crate to compile + smoke-pass within the budget. Re-run with the same name to re-enter the dialog (edit-mode) and adjust the intent: expand the smoke window, swap the mechanism, or drop the offending sub-feature.
- **`crate <X> is not in the allowed-crate whitelist`** in build feedback — the LLM emitted a dep outside `crates/build-pipeline/whitelist.toml`. The repair loop usually fixes this on the next attempt. If the crate is genuinely needed, an operator adds it to the whitelist (out-of-band) and re-runs author.
- **`smoke_failed: no_trades`** — the emitted strategy compiled and ran without panic but did not place any simulated trades over the smoke window. The dialog will likely propose loosening an entry filter or extending the window; you can also propose either directly when control returns to the dialog.
- **`smoke_failed: timeout`** — the smoke backtest exceeded the default 60s budget. The smoke window is probably too large or the strategy is doing per-bar `O(n²)` work; have the LLM emit a tighter window and a more direct implementation.

### See also

- **Tutorial** — [Author a strategy](../tutorials/author-a-strategy.md): end-to-end walkthrough from a NL seed.
- **How-to** — [Author a strategy](author-a-strategy.md): task-oriented recipe page with deeper coverage of edit-mode and `--verify=batch`.

---

## Hypothesis loop — propose, test, decide

### What is the hypothesis loop?

A LangGraph workflow over an immutable pydantic state that runs `diagnose → kb_query → generate → critique → rank → select`. Each iteration of the inner `generate → critique → rank` cycle produces fresh hypothesis candidates informed by KB retrievals; the loop exits when (a) enough candidates pass critique, (b) the iteration budget is exhausted, or (c) new candidates closely resemble prior rejections.

Each emitted hypothesis carries: a name, the metric it targets, a **falsification criterion**, the proposed change (parameter diff *or* new Rust source), KB citations, and a lift confidence. The Tester then translates each accepted hypothesis into a parameter diff or a new strategy artifact, runs lint + smoke + full batch, and reports a verdict.

Every accepted *and* rejected decision is persisted to the ledger with its rationale; subsequent loop runs read the decision log so the loop doesn't re-propose what was already rejected.

### CLI usage

```bash
# Quick path: smoke-run the crate at its default params as the baseline.
strategy-gpt hypothesize spy_atr --baseline-defaults

# Rigorous path: lift the baseline from a prior optimize run.
strategy-gpt hypothesize spy_atr --baseline-from <opt-run-id>
```

`--baseline-from` and `--baseline-defaults` are mutually exclusive; one of the two MUST be supplied. The success-path stdout is a JSON envelope mirroring `HypothesizeResult` (`strategy`, `accepted`, `rejected`, `termination_reason`, `iterations`, `backtests_consumed`, `persisted_decision_ids`) plus the `baseline_source` label so downstream tooling can see what was compared against.

### Baseline modes

- `--baseline-defaults`: the wiring builds an `evaluate_fold` over the crate's `smoke.toml` (or `experiment.yaml` if present), then invokes it at the parameter defaults declared in `intent.toml.param_schema_sketch`. Cheapest path; the comparison space matches the candidates' but the baseline is less rigorous than an optimized one.
- `--baseline-from <opt-run-id>`: the wiring reads `best.json` (+ per-fold `oos_metrics`) from `ledger/optimizations/<opt-run-id>/`. Use after a real optimize run; the per-fold scores come from the OOS folds the optimizer cross-validated.

The `baseline_source` field in the result envelope is `"baseline_defaults"` or `"optimize_run:<id>"`.

### Notable flags

- `--objective <metric>` — objective metric the workflow optimizes against (default `sharpe`).
- `--engine-worker <path>` — engine-worker binary used for fold submissions (default `crates/target/debug/engine-worker`; build it via `cd crates && cargo build -p engine-worker`).
- `--cache-root` / `--work-root` / `--gateway-root` — build-pipeline cache, scratch dir, and gateway cache. Default to `cache/builds`, `cache/build-work`, `cache/`.
- `--kb-store <path>` — path to the SQLite-backed KB store. Default `kb/store/`; the store is built lazily from `kb/sources.toml` on first run with a one-time progress banner. `--rebuild-kb` forces a rebuild.
- `--model-stage1` / `--model-stage2` / `--model-stage3` / `--model-critique` / `--model-rank` — per-stage reasoning model overrides. Defaults pick the most capable model the env's API keys allow.
- `--llm-critic` — opt into the LLM verdict critic (deterministic critic is the default; full LLM critic surface is a follow-up — the flag currently falls back to the deterministic critic with a warning).
- `--quick` — single-fold evaluator, small mini-optimize budget. Useful for iteration.
- `--quiet` — suppress the per-node + per-LLM-attempt + per-trial heartbeats on stderr. The stdout JSON envelope is unchanged.
- `--dry-run` — print the resolved dep summary (baseline source, fold source, per-stage models, engine-worker path, budgets) without invoking the workflow.

### Decision outcomes (logic vs mechanical)

Each candidate persists with one of three outcomes in the per-strategy ledger:

| Outcome  | Trigger | Affects future ideation? |
|----------|---------|---------------------------|
| `accepted` | Workflow accepted | Yes |
| `rejected` | **Logic** failure (`reject_schema`, `reject_smoke`, `reject_noise`, `reject_variance`, `reject_verdict`) | Yes — `cheap_critique`'s duplicate-similarity check biases against similar ideas |
| `deferred` | **Mechanical** failure (`reject_build`, `reject_lint`, `reject_format`, `reject_deps`, `exhausted_repair_budget` on stages 1–3) | **No** — the LLM couldn't compile the code; the hypothesis is preserved and future runs may re-propose it |

The progress renderer reports deferred separately:
`• rank: 0 accepted, 1 rejected, 2 deferred so far`.

### Stage-3 repair behavior

Each generate stage runs through a bounded repair loop. On a retry the
LLM sees:

- The validator's **verbatim error** (rustc output, lint reasons, schema mismatch, …)
- The **previous emission** verbatim (capped at 8 KiB head + tail)

so it can patch in place rather than re-emit blind. After
`k_repair=2` total retries (3 attempts), the candidate is recorded as
**deferred** with the actual rustc / lint error in the rationale
(previously a generic placeholder).

### Per-candidate library binding

Stage-3 compiles each candidate to its own shared library. The
orchestrator routes mini-optimize through the **candidate's** library
via an `evaluate_fold_factory` rather than reusing the baseline's, so
per-trial backtests actually exercise the new strategy code. Without
this, every candidate scored identically and the mechanical gate
rejected all changes as zero-delta noise.

### Prerequisites

- The strategy crate has been authored (`strategy-gpt author <name>`).
- `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` is set.
- The engine-worker binary has been built (`cd crates && cargo build -p engine-worker`).
- For `--baseline-from`, an optimize run for the strategy exists under `ledger/optimizations/`.

Failure modes (missing crate, missing `intent.toml`, no baseline flag, no API key, missing engine-worker, missing optimize-run id) surface as `typer` errors on stderr with `exit_code=2`.

### Minimum end-to-end loop (one strategy improvement cycle)

```
1. strategy-gpt author <name>             # author the crate (interactive)
2. cd crates && cargo build -p engine-worker  # one-time
3. strategy-gpt hypothesize <name> --baseline-defaults  # propose, test, decide
4. strategy-gpt recent-decisions          # inspect what got accepted / rejected
5. strategy-gpt optimize --spec ...       # once you have an accepted hypothesis
6. strategy-gpt hypothesize <name> --baseline-from <opt-run-id>  # next iteration
```

---

## Parameter optimization

`strategy-gpt optimize` drives a per-fold search: for each fold of the experiment's `folds` block the configured method (`recursive_grid` by default, also `grid` / `random`) is run against the fold's *train* slice; every fold winner is then cross-validated across every fold's *OOS* slice and the candidate with the highest OOS-aggregate score wins. Trial rows, the run manifest, and a `best.json` land under `ledger/optimizations/<opt_id>/`; a SQLite index at `ledger/optimizations.sqlite` lists every run.

The experiment-spec carries the search definition. A minimal example lives at `examples/vxx/experiment.yaml`:

```yaml
optimize:
  method: recursive_grid
  seed: 42
  aggregator: mean
  space:
    vol_lo: { type: float, low: 0.20, high: 0.50 }
    vol_hi: { type: float, low: 0.70, high: 1.30 }
  recursive_grid:
    resolution: 10
    top_k: 1
    depth: 5
    plateau_epsilon: 0.0001
  persist:
    root: ./ledger
    name: vxx-recursive-grid

folds:
  count: 4
  scheme: rolling
```

The objective spec (primary / secondary / tradeoff) lives next to the experiment-spec as `objective.yaml`; pass `--objective <path>` to override.

```bash
# Drive the full optimization
strategy-gpt optimize --spec examples/vxx/experiment.yaml

# Predict cost and ledger footprint before launching
strategy-gpt optimize --spec examples/vxx/experiment.yaml --benchmark --sample 3 --yes

# Inspect a finished run
strategy-gpt optimize inspect <opt_id>
strategy-gpt optimize inspect <opt_id> --trial 4271     # one trial row

# Replay a single trial (byte-identical BacktestResult)
strategy-gpt optimize replay <opt_id> --trial 4271 --out result.json
```

`--method recursive_grid|grid|random|sobol|differential_evolution|cma_es|successive_halving|lhs_polish` overrides the method on the fly. `--parallelism auto` (default for the VXX example) resolves to `max(1, usable_cpus - 1)` and is recorded in the optimization manifest.

### Sobol quasi-random (drop-in replacement for `random`)

Owen-scrambled Sobol covers the parameter space more uniformly than
random sampling at the same budget. Use as a stronger baseline or as
the seed for evolutionary methods.

```bash
# Inline override of method + Sobol budget
strategy-gpt optimize --spec experiment.yaml --method sobol

# Or declare in the spec
optimize:
  method: sobol
  sobol:
    n_points: 256       # power of two
    scramble: true
    owen_seed: 42
  persist: { root: ./ledger, name: vxx-sobol }
```

### LHS + Hooke-Jeeves polish (small-budget baseline)

Latin Hypercube seeds the space, Hooke-Jeeves polishes from the top-K
LHS points. Per-iteration cost is `top_k * 2 * D` runs.

```bash
strategy-gpt optimize --spec experiment.yaml --method lhs_polish

# Or declare in the spec
optimize:
  method: lhs_polish
  lhs_polish:
    lhs_n: 128
    top_k_polish: 4
    polish: hooke_jeeves
    initial_step: 0.1
    step_min: 0.001
  persist: { root: ./ledger, name: vxx-lhs }
```

### Successive Halving (multi-fidelity)

Evaluates many candidates on a small fold subset, halves the bottom of
the rank by `1/eta`, doubles the fold budget, repeats. Cheaper than
flat per-fold search when most candidates are obviously bad.

```bash
strategy-gpt optimize --spec experiment.yaml --method successive_halving

# Or declare in the spec
optimize:
  method: successive_halving
  successive_halving:
    initial_candidates: 64
    eta: 3
    initial_folds: 2
  persist: { root: ./ledger, name: vxx-sh }
```

### CMA-ES (Hansen)

Covariance Matrix Adaptation Evolution Strategy — strong on
smooth-but-noisy continuous surfaces, especially when parameters
interact. Each generation packs as one engine batch.

```bash
strategy-gpt optimize --spec experiment.yaml --method cma_es

# Or declare in the spec
optimize:
  method: cma_es
  cma_es:
    popsize: auto       # 4 + floor(3 * ln(D))
    sigma0: 0.3
    n_generations: 50
  persist: { root: ./ledger, name: vxx-cma }
```

### Differential evolution (Storn & Price)

Population-based search; each generation packs as one engine batch. Best
on noisy, multi-modal surfaces with mixed-integer dims.

```bash
# Sobol-seeded DE with default knobs
strategy-gpt optimize --spec experiment.yaml --method differential_evolution

# Or declare in the spec
optimize:
  method: differential_evolution
  differential_evolution:
    popsize: auto       # 15 * D
    n_generations: 50
    init: sobol
  persist: { root: ./ledger, name: vxx-de }
```

### Overfitting-aware selection

Every optimization runs an overfitting-aware selection layer over its
`trials.parquet` before publishing `best.json`. See the methodology in
[Overfitting & selection](../explanation/overfitting-and-selection.md)
and operator actions in [Interpret PBO rejection](interpret-pbo-rejection.md).
Common recipes:

```bash
# Rank final by robust score (parameter-sensitivity) instead of DSR
strategy-gpt optimize --spec experiment.yaml --robust-objective

# Tighten / loosen the PBO rejection threshold
strategy-gpt optimize --spec experiment.yaml --pbo-threshold 0.3
strategy-gpt optimize --spec experiment.yaml --pbo-threshold 0.7

# Publish a best despite a rejected_pbo decision (records the override)
strategy-gpt optimize --spec experiment.yaml --force

# Re-run the selection layer post-hoc with different knobs
strategy-gpt optimize reselect <opt_id> --pbo-threshold 0.7
strategy-gpt optimize reselect <opt_id> --robust-objective
strategy-gpt optimize reselect <opt_id> --top-k 30 --robust-objective

# Compare two selection outputs from the same run
strategy-gpt optimize compare <opt_id> best.json best_<timestamp>.json
```

`reselect` writes a new `best_<timestamp>.json` next to the original
without overwriting it — the audit trail of selection decisions over the
same trial set is always preserved.

---

## Quick reference

| Goal | Command |
|------|---------|
| Print version | `strategy-gpt version` |
| Fetch dataset | `strategy-gpt fetch --provider yfinance --symbol <SYM> --start <D> --end <D> --resolution Day --adjustment back_adjusted` |
| Cache stats | `strategy-gpt cache-stats --root cache` |
| Recent decisions | `strategy-gpt recent-decisions --root ledger --limit 25` |
| Replay a run | `strategy-gpt replay --run-id <id>` |
| Submit batch (await) | `strategy-gpt run --spec <experiment.yaml> --wait` |
| Submit batch (async) | same, drop `--wait` (returns handle) |
| KB ingest | *(CLI stub; drive via Python)* |
| Hypothesis loop | *(CLI stub; drive via Python — see above)* |
| Optimize | `strategy-gpt optimize --spec <experiment.yaml>` |
| Benchmark before optimizing | `strategy-gpt optimize --spec <experiment.yaml> --benchmark --yes` |
| Inspect an optimization | `strategy-gpt optimize inspect <opt_id>` |
| Replay a recorded trial | `strategy-gpt optimize replay <opt_id> --trial <trial_id>` |
| Robust-rank selection | `strategy-gpt optimize --spec <experiment.yaml> --robust-objective` |
| Override PBO threshold | `strategy-gpt optimize --spec <experiment.yaml> --pbo-threshold 0.7` |
| Force despite rejected_pbo | `strategy-gpt optimize --spec <experiment.yaml> --force` |
| Post-hoc reselect | `strategy-gpt optimize reselect <opt_id> --pbo-threshold 0.7` |
| Compare selections | `strategy-gpt optimize compare <opt_id> best.json best_<ts>.json` |
