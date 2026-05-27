# Your first backtest

## Learning goal

Run the bundled VXX reference strategy end-to-end and read the resulting `BacktestResult`.

## Prerequisites

- A working clone of `strategy-gpt`.
- Python 3.11+ with `pip` and a venv.
- A Rust toolchain matching `rust-toolchain.toml` (1.82.0).
- `maturin` (installed automatically by `pip install -e 'python/[dev]'`).
- Network access to `yfinance` for the first fetch. The cache then makes every subsequent run offline-capable.

## Walkthrough

### 1. Install the Python orchestrator and native bindings

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e 'python/[dev]'
maturin develop -m crates/py-bindings/Cargo.toml
```

Expected: a series of build lines ending with

```
📦 Built wheel for CPython 3.11 ...
🛠 Installed strategy-gpt-...
```

### 2. Build the VXX reference strategy and the engine worker

```bash
cd crates && cargo build -p vxx-strategy -p engine --bin engine-worker && cd -
```

Expected (last lines):

```
   Compiling vxx-strategy v0.1.0 ...
   Compiling engine v0.1.0 ...
    Finished `dev` profile [unoptimized + debuginfo] target(s) in ...
```

This produces `crates/target/debug/libvxx_strategy.dylib` (the strategy artifact) and `crates/target/debug/engine-worker` (the subprocess the orchestrator drives).

### 3. Write an experiment-spec

Save the following as `first-backtest.yaml` in the repo root:

```yaml
artifact: crates/target/debug/libvxx_strategy.dylib
strategy_label: vxx-tutorial

bars:
  request:
    provider: yfinance
    symbol: VXX
    start: 2018-01-01T00:00:00Z
    end:   2024-12-31T00:00:00Z
    resolution: Day
    adjustment: back_adjusted

engine:
  fill_model: NextBarOpen
  initial_capital: 100000.0

runs:
  - params:
      vol_lo: 0.01
      vol_hi: 0.04
      size:   100.0
      symbol: VXX
    modes:
      - { kind: plain }
    seed: 42
    slice:
      start: 2018-01-01T00:00:00Z
      end:   2024-12-31T00:00:00Z

parallelism: 1
caps:
  time_cap_secs: 120
```

No stdout output (this step writes a file).

### 4. Run the backtest

```bash
strategy-gpt run --spec first-backtest.yaml --wait
```

Expected (truncated):

```json
{
  "status": "completed",
  "results": [
    {
      "status": "ok",
      "run_index": 0,
      "result": {
        "meta": {
          "strategy_artifact": "vxx-tutorial",
          "dataset_manifest": "c9ef4c864ecef659...",
          "seed": 42,
          "runner_version": { "major": 0, "minor": 1, "patch": 0 }
        },
        "metrics": {
          "sharpe": 0.0,
          "sortino": 0.0,
          "profit_factor": 0.0,
          "win_ratio": 0.0,
          "max_drawdown": 0.0,
          "annualized_return": 0.0,
          "n_trades": 0,
          "avg_trade_length_bars": 0.0
        },
        "trades":   [],
        "signals":  [...],
        "equity":   [...],
        "regimes":  [...],
        "exec_log": [...],
        "stress":   null,
        "sensitivity": null
      }
    }
  ],
  "error": null
}
```

The first run pulls VXX history from yfinance (a few seconds) and caches it under `cache/`. Subsequent runs over the same window are offline. The `metrics` block is zero whenever the strategy did not trade in the slice — with the defaults above on this window, `realized_vol_20` does not cross the `vol_lo=0.01` entry threshold, so no positions open. That is a perfectly valid backtest outcome; you confirm the loop is working by inspecting `signals` and `exec_log` instead of the metrics.

### 5. Inspect what the strategy did

```bash
strategy-gpt run --spec first-backtest.yaml --wait | jq '.results[0].result.signals[0]'
```

Expected:

```json
{
  "name": "vol_value",
  "ts": "2018-01-02T14:30:00Z",
  "value": 0.0,
  "fired": false,
  "suppressed_by": "indicator_warmup"
}
```

`results[0].result.signals` carries every `vol_value` / `enter_short` / `exit_short` / suppressed signal the strategy emitted, in timestamp order. `exec_log` carries the `init` / `end` decision events from `ctx.log_decision(...)`. Both are how you debug a run when the metrics look wrong.

## What you just did

You exercised the full single-run path: Python orchestrator (`strategy-gpt run`) parsed an experiment-spec, the data gateway resolved bars from yfinance into a year-segmented content-addressed cache, the engine launched an `engine-worker` subprocess, the worker `dlopen`ed `libvxx_strategy.dylib` and drove its `Strategy` lifecycle over every bar, and the orchestrator pretty-printed the `BacktestResult`. The same path is what the optimizer and the hypothesis loop call into; every other workflow in this repository is a fan-out on top of it.

## What next

- **Walkthrough** — [Guided CLI walkthrough → Stage 4 (One-shot backtest)](../guided-cli-walkthrough.md#stage-4-one-shot): the full set of `strategy-gpt run` recipes (CSV providers, cache modes, multi-run sweeps).
- **Reference** — [`experiment-spec`](../reference/experiment-spec.md): every field on the YAML you just wrote, including `bars.dataset` (cache-only) and the `modes` axis.
- **Reference** — [`BacktestResult`](../reference/batch-spec.md): the full output schema, including `signals`, `equity`, `exec_log`, `regimes`.
- **Explanation** — [Architecture](../explanation/architecture.md): the orchestrator/Rust/worker split you just ran through, and why the trust boundaries are where they are.
- **Tutorial** — [Walking the hypothesize loop](hypothesize-loop.md): drive the `hypothesize`, `hypothesis replay`, and `hypothesis diff` CLI surface against an in-repo fixture ledger.
