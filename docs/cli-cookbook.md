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
| `validate`      | Re-fetch and diff against the cached blob; emit divergence warnings on disagreement. Treat as `prefer_cache` in v1 with a follow-up. |
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

```bash
python -c "
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
"
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
| `--spec`              | Path to an `experiment-spec` YAML or JSON. See [experiment-spec reference](./experiment-spec.md). The spec carries `artifact`, `bars` (`dataset` or `request`), `engine`, `runs`, `parallelism`, and `caps`. |
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

`status` is one of `completed | failed | cancelled`. On failure `error` is populated and `results` is null. See [BatchSpec JSON reference — `BacktestResult`](./batch-spec.md) for the full output schema.

Each `results[i]` is a `RunResult` discriminated entry — successful runs carry `{ status: "ok", run_index, result }`; failed runs (only emitted under `failure_mode: continue`) carry `{ status: "failed", run_index, error_kind, message }`. `strategy-gpt run` defaults to `failure_mode: abort`, so a single bad run still surfaces as an outer `failed` job; opt into `continue` from a packed `BatchSpec` when you want per-run isolation (the optimizer's primary use case).

### Submit without waiting (manual polling)

```bash
HANDLE=$(strategy-gpt run --spec examples/vxx/experiment.yaml)
echo "submitted: $HANDLE"
# Poll later via the Python engine surface; CLI poll subcommand lands with phase 13.
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

Add more entries to the spec's `runs:` list. The engine compiles once and fans out across `parallelism` worker subprocesses. See [BatchSpec JSON reference — Multi-run example](./batch-spec.md#multi-run-example-parameter-sweep) for the internal shape that `experiment-spec` translates into.

---

## Hypothesis loop — propose, test, decide

### What is the hypothesis loop?

A LangGraph workflow over an immutable pydantic state that runs `diagnose → kb_query → generate → critique → rank → select`. Each iteration of the inner `generate → critique → rank` cycle produces fresh hypothesis candidates informed by KB retrievals; the loop exits when (a) enough candidates pass critique, (b) the iteration budget is exhausted, or (c) new candidates closely resemble prior rejections.

Each emitted hypothesis carries: a name, the metric it targets, a **falsification criterion**, the proposed change (parameter diff *or* new Rust source), KB citations, and a lift confidence. The Tester then translates each accepted hypothesis into a parameter diff or a new strategy artifact, runs lint + smoke + full batch, and reports a verdict.

Every accepted *and* rejected decision is persisted to the ledger with its rationale; subsequent loop runs read the decision log so the loop doesn't re-propose what was already rejected.

### CLI status — currently stubbed

```bash
strategy-gpt hypothesize
# `hypothesize` is not implemented yet; lands with phase 9 (hypothesis-loop).
```

The CLI subcommand is reserved but the driver isn't wired. Until it lands, drive the loop from Python directly.

### Python invocation pattern

```python
from datetime import UTC, datetime
from strategy_gpt.diagnose import diagnose
from strategy_gpt.hypothesis_loop import (
    HypothesisLoopState, bootstrap_state_from_ledger,
)
from strategy_gpt.kb_query import kb_query_node
from strategy_gpt.ledger import Ledger
from strategy_gpt.nodes import run_inner_loop
from strategy_gpt.reasoning import HypothesisLoopConfig, select_reasoning_model

# 1. Load prior decisions so the loop doesn't re-propose rejected ideas.
ledger = Ledger("ledger")
state = bootstrap_state_from_ledger(ledger)

# 2. Diagnose a recent backtest result (from `strategy-gpt run --wait`).
state = diagnose(state, backtest_result=last_result)

# 3. Retrieve KB context relevant to the diagnosis.
state = kb_query_node(state, kb_client=kb)

# 4. Run generate → critique → rank → select.
config = HypothesisLoopConfig(target_candidates=3, iteration_budget=5)
model = select_reasoning_model()              # picks Anthropic or OpenAI based on env
client = ...                                  # ReasoningClient backed by `model`
state = run_inner_loop(state, client=client, config=config)

# 5. Hand the accepted hypotheses to the Tester to produce verdicts.
for hyp in state.accepted:
    print(hyp.name, hyp.falsification_criterion, hyp.proposed_change)
```

Requires `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` (see [.envrc.example](../.envrc.example)). The smoke fixture (`python -m strategy_gpt.smoke`) stubs the reasoning client and runs offline; consult [`python/strategy_gpt/smoke.py`](../python/strategy_gpt/smoke.py) for the full mock wiring.

### Minimum end-to-end loop (one strategy improvement cycle)

```
1. strategy-gpt fetch              # dataset (one-time per window)
2. (materialize bars JSON)         # one-time per window
3. strategy-gpt run --wait         # baseline backtest
4. Python: diagnose → kb_query → generate → critique → rank → select
5. (Tester translates hypothesis to artifact + smoke + full batch)
6. strategy-gpt recent-decisions   # inspect what got accepted / rejected
7. (loop)                          # verdict feeds the next diagnose
```

Steps 4-5 collapse into `strategy-gpt hypothesize` once the CLI driver lands.

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

`--method recursive_grid|grid|random|sobol|differential_evolution|cma_es|successive_halving` overrides the method on the fly. `--parallelism auto` (default for the VXX example) resolves to `max(1, usable_cpus - 1)` and is recorded in the optimization manifest.

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
`trials.parquet` before publishing `best.json`. See `docs/optimization.md`
for the methodology. Common recipes:

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
| KB ingest | *(stub — phase 8)* |
| Hypothesis loop | *(stub — drive via Python; see above)* |
| Optimize | `strategy-gpt optimize --spec <experiment.yaml>` |
| Benchmark before optimizing | `strategy-gpt optimize --spec <experiment.yaml> --benchmark --yes` |
| Inspect an optimization | `strategy-gpt optimize inspect <opt_id>` |
| Replay a recorded trial | `strategy-gpt optimize replay <opt_id> --trial <trial_id>` |
| Robust-rank selection | `strategy-gpt optimize --spec <experiment.yaml> --robust-objective` |
| Override PBO threshold | `strategy-gpt optimize --spec <experiment.yaml> --pbo-threshold 0.7` |
| Force despite rejected_pbo | `strategy-gpt optimize --spec <experiment.yaml> --force` |
| Post-hoc reselect | `strategy-gpt optimize reselect <opt_id> --pbo-threshold 0.7` |
| Compare selections | `strategy-gpt optimize compare <opt_id> best.json best_<ts>.json` |
