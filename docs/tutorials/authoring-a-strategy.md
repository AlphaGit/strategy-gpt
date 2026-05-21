# Authoring a strategy

## Learning goal

Implement a new strategy as a `cdylib` Rust crate against the sealed `Strategy` trait, build it through the workspace, and run it through the engine end-to-end.

## Prerequisites

- A working clone of `strategy-gpt`.
- A Rust toolchain matching `rust-toolchain.toml` (1.82.0).
- Python 3.11+ with `pip` and a venv (used to install the orchestrator that drives the engine worker).
- Familiarity with the lifecycle and `Context` API in `crates/engine-rt/PROMPT_API.md` (the same document the hypothesis loop hands to the LLM). Open it alongside this page.

## Walkthrough

### 1. Copy `example-strategy` as a scaffold

```bash
cp -r crates/example-strategy crates/my-strategy
```

No stdout output.

`example-strategy` is the smallest possible crate that compiles against the strategy ABI: a `NoopStrategy` that records lifecycle calls and ships an empty `params_schema.json`. It is the right place to start; the build pipeline expects exactly this layout.

### 2. Rename the crate so the workspace can discriminate it

Open `crates/my-strategy/Cargo.toml` and change:

```toml
[package]
name = "my-strategy"
# ... (everything else stays)

[lib]
name = "my_strategy"
crate-type = ["cdylib"]
```

Then add `crates/my-strategy` to the workspace members in `crates/Cargo.toml` (under `[workspace] members = [...]`). No stdout output.

### 3. Declare your parameters in `params_schema.json`

`params_schema.json` is the single source of truth for what your strategy reads out of `ctx.state_get("__params__")`. Replace `crates/my-strategy/params_schema.json` with:

```json
{
  "schema_version": 1,
  "params": [
    {
      "name": "lookback",
      "kind": "i64",
      "min": 5,
      "max": 200,
      "default": 20,
      "description": "Lookback window in bars for the moving-average filter."
    },
    {
      "name": "size",
      "kind": "f64",
      "min": 1.0,
      "max": 10000.0,
      "default": 100.0,
      "description": "Trade notional in instrument units."
    },
    {
      "name": "symbol",
      "kind": "string",
      "default": "VXX",
      "description": "Instrument symbol."
    }
  ]
}
```

No stdout output. Every parameter your `on_bar` reads MUST appear here; the build pipeline rejects strategies whose runtime references undeclared params.

### 4. Implement the `Strategy` trait

Replace `crates/my-strategy/src/lib.rs` with:

```rust
use engine_rt::{
    strategy_entry, Bar, Context, Fill, Result, Sealed, Side, StateKey, Strategy, StrategyMeta,
};
use serde::{Deserialize, Serialize};
use serde_json::json;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Params {
    pub lookback: i64,
    pub size: f64,
    pub symbol: String,
}

impl Default for Params {
    fn default() -> Self {
        Self { lookback: 20, size: 100.0, symbol: "VXX".into() }
    }
}

#[derive(Default)]
pub struct MyStrategy {
    params: Params,
    closes: Vec<f64>,
}

impl Sealed for MyStrategy {}

impl Strategy for MyStrategy {
    fn metadata(&self) -> StrategyMeta {
        StrategyMeta::new(
            "my_strategy",
            "0.1.0",
            "tutorial",
            "Mean-reversion sketch: short when close > SMA(lookback), exit when below.",
        )
    }

    fn on_init(&mut self, ctx: &mut dyn Context) -> Result<()> {
        let key = StateKey::from("__params__");
        if let Some(value) = ctx.state_get(&key)? {
            if !value.is_null() {
                if let Ok(p) = serde_json::from_value::<Params>(value) {
                    self.params = p;
                }
            }
        }
        ctx.log_decision("init", json!({ "lookback": self.params.lookback }));
        Ok(())
    }

    fn on_bar(&mut self, bar: &Bar, ctx: &mut dyn Context) -> Result<()> {
        if bar.symbol != self.params.symbol {
            return Ok(());
        }
        self.closes.push(bar.close);
        let n = self.params.lookback as usize;
        if self.closes.len() < n {
            ctx.log_signal("sma", 0.0, false, Some("warmup"));
            return Ok(());
        }
        let sma: f64 = self.closes[self.closes.len() - n..].iter().sum::<f64>() / (n as f64);
        ctx.log_signal("sma", sma, true, None);

        let pos = ctx.get_position(&self.params.symbol);
        let is_short = pos.size < 0.0;
        if !is_short && bar.close > sma {
            ctx.submit_order(
                &self.params.symbol, Side::Short, self.params.size,
                None, None, Some("above_sma"),
            )?;
        } else if is_short && bar.close < sma {
            ctx.submit_order(
                &self.params.symbol, Side::Long, self.params.size,
                None, None, Some("below_sma_exit"),
            )?;
        }
        Ok(())
    }

    fn on_fill(&mut self, _fill: &Fill, _ctx: &mut dyn Context) -> Result<()> {
        Ok(())
    }

    fn on_end(&mut self, ctx: &mut dyn Context) -> Result<()> {
        ctx.log_decision("end", json!({ "bars_seen": self.closes.len() }));
        Ok(())
    }
}

fn factory() -> Box<dyn Strategy> { Box::<MyStrategy>::default() }

strategy_entry!(factory);
```

The shape is fixed: `Default`, `Sealed`, `Strategy`, a `factory()` returning `Box<dyn Strategy>`, and the `strategy_entry!(factory)` macro that emits the C-ABI symbols the worker resolves. You never write the FFI by hand.

The `Context` capability handle is your *only* doorway into engine-managed state — orders, positions, indicators, signals, decisions. Filesystem, network, threads, raw FFI, and panicking on engine-provided data are all forbidden by the linter (see PROMPT_API.md §8).

### 5. Build the crate

```bash
cargo build -p my-strategy --manifest-path crates/Cargo.toml
```

Expected (last lines):

```
   Compiling my-strategy v0.1.0 (.../crates/my-strategy)
    Finished `dev` profile [unoptimized + debuginfo] target(s) in ...
```

This produces `crates/target/debug/libmy_strategy.dylib`. The crate is now a valid strategy artifact the engine can load.

### 6. Run it end-to-end

Save the following as `my-strategy.yaml` in the repo root:

```yaml
artifact: crates/target/debug/libmy_strategy.dylib
strategy_label: my-strategy-tutorial

bars:
  request:
    provider: yfinance
    symbol: VXX
    start: 2020-01-01T00:00:00Z
    end:   2023-12-31T00:00:00Z
    resolution: Day
    adjustment: back_adjusted

engine:
  fill_model: NextBarOpen
  initial_capital: 100000.0

runs:
  - params:
      lookback: 20
      size:     100.0
      symbol:   VXX
    modes:
      - { kind: plain }
    seed: 42
    slice:
      start: 2020-01-01T00:00:00Z
      end:   2023-12-31T00:00:00Z

parallelism: 1
caps:
  time_cap_secs: 120
```

Then:

```bash
strategy-gpt run --spec my-strategy.yaml --wait | jq '.results[0].result.meta'
```

Expected:

```json
{
  "strategy_artifact": "my-strategy-tutorial",
  "dataset_manifest": "c9ef4c864ecef659...",
  "seed": 42,
  "runner_version": { "major": 0, "minor": 1, "patch": 0 }
}
```

If `meta.strategy_artifact` is populated and `runner_version` is the current engine-rt semver, the worker loaded your `cdylib` and drove the full lifecycle. Inspect `.results[0].result.signals` to confirm `sma` signals fire after the 20-bar warm-up, and `.exec_log` to confirm `init` / `end` decision events.

## What you just did

You wrote a new strategy crate from `example-strategy`, declared its parameters in `params_schema.json` (the contract the build pipeline and the optimizer both consume), implemented the four `Strategy` lifecycle methods against the `Context` capability handle, built the `cdylib` through the same `cargo build` the LLM build pipeline uses, and ran it through the engine with `strategy-gpt run`. The artifact you produced is byte-identical in shape to one the hypothesis loop would emit — the only difference is that yours was hand-authored.

## What next

- **Reference** — [`crates/engine-rt/PROMPT_API.md`](https://github.com/AlphaGit/strategy-gpt/blob/main/crates/engine-rt/PROMPT_API.md): the authoritative lifecycle + `Context` surface. Read end-to-end before adding indicators, state, or `submit_order` complications.
- **Reference** — [`experiment-spec`](../reference/experiment-spec.md): the full schema of the YAML you wrote, including the `modes` axis (`Slippage`, `Sensitivity`) and `folds`.
- **Explanation** — [Architecture](../explanation/architecture.md): why the strategy crate runs in a worker subprocess and how it crosses the trust boundary.
- **Decision** — [ADR 0006 — Sealed `Strategy` trait](../decisions/0006-sealed-strategy-trait.md): the rationale behind the sealed-trait shape you just implemented.
- **Tutorial** — [Your first backtest](first-backtest.md): run the bundled VXX reference strategy end-to-end and read the resulting `BacktestResult`.
