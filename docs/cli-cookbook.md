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
  --spec examples/vxx/batch.json \
  --artifact crates/target/debug/libvxx_strategy.dylib \
  --worker crates/target/debug/engine-worker \
  --bars examples/vxx/bars.json \
  --dataset-manifest 29bdecf5fe758d38d524025321aacfb2825daf2fbcce4a3c2c04377bf635b97b \
  --wait --time-cap-secs 120
```

| Flag | Purpose |
|---|---|
| `--spec`              | Path to a `BatchSpec` JSON. See [BatchSpec JSON reference](./batch-spec.md). |
| `--artifact`          | Compiled strategy `cdylib` produced by the build pipeline. |
| `--worker`            | `engine-worker` binary; the orchestrator spawns one subprocess per `RunSpec`. |
| `--bars`              | JSON list of bars (output of the materialize step above). |
| `--dataset-manifest`  | Manifest hash from `strategy-gpt fetch`; opaque passthrough to the ledger. |
| `--wait`              | Block until job completion; print full `JobStatus` JSON. Without it: print the handle and return immediately. |
| `--time-cap-secs`     | Per-run wall-clock cap. Workers exceeding it are killed by the coordinator. |
| `--mem-cap-bytes`     | Per-run memory cap (Linux). |
| `--poll-interval-secs`| Poll interval when `--wait` is set. Default `0.5`. |

`JobStatus` shape returned by `--wait`:

```json
{ "status": "completed",
  "results": [
    { "metrics":   { "sharpe": ..., "max_drawdown": ..., "n_trades": ..., ... },
      "trades":    [...],
      "signals":   [...],
      "equity":    [...],
      "regimes":   [...],
      "exec_log":  [...],
      "meta":      { "artifact_hash": "...", "dataset_manifest": "...", "seed": ..., "runner_version": "..." }
    }
  ],
  "error": null
}
```

`status` is one of `completed | failed | cancelled`. On failure `error` is populated and `results` is null. See [BatchSpec JSON reference — `BacktestResult`](./batch-spec.md) for the full output schema.

### Submit without waiting (manual polling)

```bash
HANDLE=$(strategy-gpt run \
  --spec examples/vxx/batch.json \
  --artifact crates/target/debug/libvxx_strategy.dylib \
  --worker crates/target/debug/engine-worker \
  --bars examples/vxx/bars.json \
  --dataset-manifest <hash>)
echo "submitted: $HANDLE"
# Poll later via the Python engine surface; CLI poll subcommand lands with phase 13.
```

### Tweak parameters without recompiling

Edit `examples/vxx/batch.json`:

```jsonc
"params": {
  "vol_lo": 0.35,    // change me
  "vol_hi": 0.80,    // change me
  "size":   100.0,
  "symbol": "VXX"
}
```

Re-run `strategy-gpt run`. The artifact hash is unchanged; the engine reuses the compiled `.dylib`. Parameter changes never trigger a rebuild — that's what `params` is for.

### Multi-run sweep

Add more `RunSpec` entries to `batch.json`'s `runs` array. The engine compiles once and fans out across `parallelism` worker subprocesses. See [BatchSpec JSON reference — Multi-run example](./batch-spec.md#multi-run-example-parameter-sweep).

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

> **TBD.** `strategy-gpt optimize` is currently stubbed and the driver wiring is unfinished. An experimental script-level runner lives at [`examples/vxx/optimize.py`](../examples/vxx/optimize.py); a final CLI command and its documented options will land here once the optimizer driver is wired into the CLI.

---

## Quick reference

| Goal | Command |
|------|---------|
| Print version | `strategy-gpt version` |
| Fetch dataset | `strategy-gpt fetch --provider yfinance --symbol <SYM> --start <D> --end <D> --resolution Day --adjustment back_adjusted` |
| Cache stats | `strategy-gpt cache-stats --root cache` |
| Recent decisions | `strategy-gpt recent-decisions --root ledger --limit 25` |
| Replay a run | `strategy-gpt replay --run-id <id>` |
| Submit batch (await) | `strategy-gpt run --spec <s.json> --artifact <a.dylib> --worker <w> --bars <b.json> --dataset-manifest <h> --wait` |
| Submit batch (async) | same, drop `--wait` (returns handle) |
| KB ingest | *(stub — phase 8)* |
| Hypothesis loop | *(stub — drive via Python; see above)* |
| Optimize | *(stub — TBD)* |
