# engine-rt PROMPT_API

Authoritative LLM context for generating strategy crates inside the strategy-gpt
hypothesis loop. This document is embedded verbatim into every reasoning call
that emits strategy code. The hypothesis loop assumes this document is the
single source of truth — keep it synchronized with `crates/engine-rt/src/*` on
every change to the public surface.

This file is hand-maintained. `cargo doc` output is too verbose and lacks the
"how to write a strategy" framing the LLM needs.

---

## 1. What a strategy is

A strategy is a `cdylib` Rust crate that depends only on whitelisted crates
(see §6) and exposes one type implementing the sealed [`Strategy`] trait. The
build pipeline lays out the crate, compiles it, caches the artifact, and the
engine worker loads it as a dynamic library to drive the backtest.

You write **only**:

- `Cargo.toml` (dependencies restricted to the whitelist).
- `src/lib.rs` (one type that implements [`Strategy`], plus the
  [`strategy_entry!`] invocation).
- `params_schema.json` at the crate root (see §4).
- Optional helper modules under `src/` (e.g. `src/indicators.rs`).

You do **not** write `unsafe`, `extern`, network code, filesystem code, threads,
or anything outside the [`Context`] capability handle (see §7).

---

## 2. File layout convention

```
<crate-root>/
  Cargo.toml          # see §6
  params_schema.json  # see §4 (REQUIRED — emit even if no params)
  src/
    lib.rs            # entry point: Strategy impl + strategy_entry!(factory)
    *.rs              # optional helper modules
```

The build pipeline reads `Cargo.toml` + every `.rs` under `src/` +
`params_schema.json` at the crate root. Anything else is ignored.

---

## 3. The `Strategy` trait

```rust
pub trait Strategy: Sealed {
    fn metadata(&self) -> StrategyMeta;

    fn on_init(&mut self, _ctx: &mut dyn Context) -> Result<()> { Ok(()) }

    fn on_bar(&mut self, bar: &Bar, ctx: &mut dyn Context) -> Result<()>;

    fn on_fill(&mut self, _fill: &Fill, _ctx: &mut dyn Context) -> Result<()> {
        Ok(())
    }

    fn on_end(&mut self, _ctx: &mut dyn Context) -> Result<()> { Ok(()) }
}
```

Lifecycle ordering:

1. **`on_init`** — called once before the first bar. Read parameters via
   `ctx.state_get(&StateKey::from("__params__"))`. Initialize any per-run
   counters or state.
2. **`on_bar`** — called once per bar in timestamp order. The only required
   method. Read indicators, decide, submit trade intents.
3. **`on_fill`** — called once per simulated fill, before the next `on_bar`.
4. **`on_end`** — called once after the last bar. Use for end-of-run
   summary logging via `ctx.log_decision`.

Your type must be `Sealed`. Use the seal helper:

```rust
impl engine_rt::Sealed for MyStrategy {}
```

Implement `Default` (or your own constructor invoked from `factory`) so the
plugin macro can build instances without arguments.

The strategy's metadata `name` should match the crate name (e.g. crate
`vxx_volatility_range` → metadata name `vxx_volatility_range`).

### Plugin entry point

Every strategy crate ends with:

```rust
fn factory() -> Box<dyn Strategy> {
    Box::<MyStrategy>::default()
}

strategy_entry!(factory);
```

`strategy_entry!` emits the three `extern "C"` symbols the engine worker
resolves (`_strategy_gpt_create`, `_strategy_gpt_drop`,
`_strategy_gpt_abi_major`). You do not write these by hand.

---

## 4. Parameter declaration convention

Every strategy crate ships a `params_schema.json` at the crate root. The file
is the **single source of truth** for which parameters the strategy reads from
`ctx.state_get("__params__")`. The build pipeline introspects this file and
the tester validates `param_intent` against it.

Schema (JSON):

```json
{
  "schema_version": 1,
  "params": [
    {
      "name": "vol_lo",
      "kind": "f64",
      "min": 0.001,
      "max": 0.05,
      "default": 0.01,
      "description": "Enter short when realized vol falls at or below this threshold."
    },
    {
      "name": "vol_hi",
      "kind": "f64",
      "min": 0.01,
      "max": 0.20,
      "default": 0.04,
      "description": "Exit short when realized vol rises at or above this threshold."
    },
    {
      "name": "size",
      "kind": "f64",
      "min": 1.0,
      "max": 10000.0,
      "default": 100.0,
      "description": "Short notional in instrument units."
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

Field semantics:

- `name` — must be a valid Rust identifier; matches the field in the strategy
  params struct that deserializes from `__params__`.
- `kind` — one of `f64`, `i64`, `bool`, `string`.
- `min`, `max` — required for numeric kinds; absent for `bool` and `string`.
- `default` — required; must be type-consistent with `kind`.
- `description` — single-line human-readable explanation.

An empty parameter set is encoded as `"params": []`. The file is still
required.

The strategy reads params at runtime via:

```rust
use engine_rt::StateKey;

let key = StateKey::from("__params__");
if let Some(value) = ctx.state_get(&key)? {
    if !value.is_null() {
        if let Ok(p) = serde_json::from_value::<MyParams>(value) {
            self.params = p;
        }
    }
}
```

The hypothesis loop's `param_intent.added` block must list `(name, min, max,
default)` for every added param; the build pipeline rejects strategies whose
runtime references undeclared params and the tester emits `reject_schema` when
`param_intent` references a name absent from `params_schema.json`.

---

## 5. Data types reachable from strategy code

All types live in the `engine_rt` crate. None of them are `mut`-shared with
the engine — strategies receive immutable references or owned copies.

### `Bar`

```rust
pub struct Bar {
    pub symbol: String,
    pub ts: chrono::DateTime<chrono::Utc>,
    pub resolution: Resolution,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}

pub enum Resolution { Minute, FiveMinute, FifteenMinute, Hour, Day, Week }
```

### `Side`, `Order`, `Fill`, `Position`, `OrderId`

```rust
pub enum Side { Long, Short }

pub struct OrderId(pub u64);

pub struct Order {
    pub id: OrderId,
    pub symbol: String,
    pub side: Side,
    pub size: f64,
    pub limit_price: Option<f64>,
    pub stop_price: Option<f64>,
    pub submitted_at: chrono::DateTime<chrono::Utc>,
    pub reason: Option<String>,
}

pub struct Fill {
    pub order_id: OrderId,
    pub symbol: String,
    pub side: Side,
    pub size: f64,
    pub price: f64,
    pub fee: f64,
    pub ts: chrono::DateTime<chrono::Utc>,
}

pub struct Position {
    pub symbol: String,
    pub size: f64,      // signed: negative for short
    pub avg_price: f64,
}
```

### `SignalEvent`, `DecisionEvent`, `StateKey`, `IndicatorName`

```rust
pub struct SignalEvent {
    pub name: String,           // signal name
    pub ts: chrono::DateTime<chrono::Utc>,
    pub value: f64,
    pub fired: bool,
    pub suppressed_by: Option<String>,
}

pub struct DecisionEvent {
    pub ts: chrono::DateTime<chrono::Utc>,
    pub event: String,
    pub details: serde_json::Value,
}

pub struct StateKey(pub String);
impl<S: Into<String>> From<S> for StateKey { /* … */ }

pub type IndicatorName = String;
```

### `Error`

```rust
pub enum Error {
    InvalidOrder(String),
    UnknownOrder(u64),
    UnknownIndicator(String),
    UnknownStateKey(String),
    RiskCap(String),
    Abort(String),
}
pub type Result<T> = std::result::Result<T, Error>;
```

You return `Result<()>` from lifecycle methods. Propagate `Context` errors
with `?`. Do not catch `RiskCap` — that one means the backtest is invalid and
must abort.

---

## 6. Allowed crates (whitelist)

The Cargo manifest may declare ONLY these crates as direct dependencies. The
build pipeline rejects any other name (direct, dev, or build). No version
pinning — Cargo resolves the latest compatible version automatically.

- `engine-rt` — the strategy ABI. **Required** for every strategy.
- `chrono` — timestamps; bar comparisons.
- `serde` — serialization for the params struct.
- `serde_json` — JSON for `ctx.log_decision` details and `__params__` parse.
- `ndarray` — numeric arrays for indicator math.
- `polars` — DataFrames. Avoid in `on_bar`; allocations matter.

Forbidden categories (these will hard-reject; do not even suggest them):

- Async runtimes: `tokio`, `async-std`, `smol`, `futures-executor`.
- HTTP / network: `reqwest`, `hyper`, `tonic`, `ureq`.
- Filesystem: `tempfile`, `glob`, anything that reads outside `Context`.
- Concurrency: `rayon`, `crossbeam`, raw `std::thread`.
- ML inference: `candle`, `tract`, `onnxruntime`.

There is no dependency-suggestion workflow. If the strategy logic seems to
require a forbidden crate, the strategy must be redesigned.

### Cargo.toml template

```toml
[package]
name = "<strategy_name>"
version = "0.1.0"
edition = "2021"

[lib]
name = "<strategy_name>"
crate-type = ["cdylib"]

[dependencies]
engine-rt = "*"
serde = { version = "*", features = ["derive"] }
serde_json = "*"
# add chrono, ndarray, polars only if used
```

The build pipeline injects `engine-rt` as a `path` dependency; the `"*"`
requirement in the user-emitted manifest is rewritten transparently.

---

## 7. The `Context` capability handle

`Context` is the strategy's only doorway into engine-managed state. Every
lifecycle method receives `&mut dyn Context`.

```rust
pub trait Context {
    /// Submit a trade intent. The engine assigns an OrderId and simulates
    /// the fill at its configured time (next bar open or current bar
    /// close). No cancellation — submit a closing intent on the next bar.
    fn submit_order(
        &mut self,
        symbol: &str,
        side: Side,
        size: f64,
        limit_price: Option<f64>,
        stop_price: Option<f64>,
        reason: Option<&str>,
    ) -> Result<OrderId>;

    /// Current accounting position used for backtest decisions.
    /// P&L is computed post-hoc by the engine — not exposed here.
    fn get_position(&self, symbol: &str) -> Position;

    /// Emit a signal observation. `fired = false` records a *suppressed*
    /// signal with the reason in `suppressed_by`.
    fn log_signal(
        &mut self,
        name: &str,
        value: f64,
        fired: bool,
        suppressed_by: Option<&str>,
    );

    /// Emit a structured decision event for the exec_log sidecar.
    /// `details` is serde JSON; common pattern: `json!({"key": value})`.
    fn log_decision(&mut self, event: &str, details: serde_json::Value);

    /// Read the latest value of an engine-provided indicator. Errors with
    /// `UnknownIndicator` if the indicator is not registered or
    /// `RiskCap`-style failures if the warm-up is incomplete.
    fn read_indicator(&self, name: &IndicatorName) -> Result<f64>;

    fn state_get(&self, key: &StateKey) -> Result<Option<serde_json::Value>>;

    fn state_set(&mut self, key: StateKey, value: serde_json::Value) -> Result<()>;
}
```

Side-effect rules:

- `submit_order` and `state_set` mutate engine state — call from `&mut self`
  methods only.
- `get_position`, `read_indicator`, `state_get` are observers; safe to call
  from anywhere a `Context` is in scope.
- `log_signal` / `log_decision` are observability hooks. They do not affect
  fills or positions; emit them liberally so diagnose has rich evidence.

Engine-provided indicator names are stable strings. The reference VXX
strategy uses `"realized_vol_20"`; consult the engine's indicator registry
for the full list at generation time (the prompt builder embeds the active
list under §10).

---

## 8. Forbidden constructs

The linter hard-rejects any strategy containing:

- `unsafe` blocks or `unsafe fn`.
- `extern "C"` (the `strategy_entry!` macro emits these for you).
- Direct FFI: raw pointers, `*mut`, `*const` to opaque types.
- Network: any `std::net::*` import; any whitelisted crate's network feature.
- Filesystem: `std::fs::*`, `std::io::*` (other than `serde_json` parsing
  from in-memory strings).
- Threads: `std::thread::*`, `std::sync::Mutex`, `Arc`, `Rc`.
- Time other than what `Bar.ts` provides: `std::time::*`,
  `chrono::Utc::now()`.
- Panics in normal flow: `unwrap()` on an `Option` that could be `None`
  during normal warmup, `expect()` on engine-provided data. Prefer
  `match` / `if let` and emit a suppressed `log_signal` for warm-up gaps.
- Global mutable state: `static mut`, `lazy_static!` with mutability, RC
  cells. Carry mutable state on `self`.

---

## 9. Minimal end-to-end exemplar

A strategy that holds VXX short when realized vol is low and exits when it
rises. Mirrors `crates/vxx-strategy/src/lib.rs`. Use this shape verbatim as
your scaffold.

### `Cargo.toml`

```toml
[package]
name = "vxx_volatility_range"
version = "0.1.0"
edition = "2021"

[lib]
name = "vxx_volatility_range"
crate-type = ["cdylib"]

[dependencies]
engine-rt = "*"
serde = { version = "*", features = ["derive"] }
serde_json = "*"
```

### `params_schema.json`

(See §4 — same shape as the example there.)

### `src/lib.rs`

```rust
use engine_rt::{
    strategy_entry, Bar, Context, Fill, Result, Sealed, Side, StateKey, Strategy,
    StrategyMeta,
};
use serde::{Deserialize, Serialize};
use serde_json::json;

const REALIZED_VOL_INDICATOR: &str = "realized_vol_20";

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Params {
    pub vol_lo: f64,
    pub vol_hi: f64,
    pub size: f64,
    pub symbol: String,
}

impl Default for Params {
    fn default() -> Self {
        Self { vol_lo: 0.01, vol_hi: 0.04, size: 100.0, symbol: "VXX".into() }
    }
}

#[derive(Default)]
pub struct VxxStrategy {
    params: Params,
    warmup_bars_seen: u32,
}

impl Sealed for VxxStrategy {}

impl Strategy for VxxStrategy {
    fn metadata(&self) -> StrategyMeta {
        StrategyMeta::new(
            "vxx_volatility_range",
            "0.1.0",
            "strategy-gpt",
            "Short-VXX-in-contango reference smoke strategy.",
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
        ctx.log_decision("init", json!({
            "vol_lo": self.params.vol_lo,
            "vol_hi": self.params.vol_hi,
            "size": self.params.size,
            "symbol": self.params.symbol,
        }));
        Ok(())
    }

    fn on_bar(&mut self, bar: &Bar, ctx: &mut dyn Context) -> Result<()> {
        if bar.symbol != self.params.symbol {
            return Ok(());
        }
        self.warmup_bars_seen = self.warmup_bars_seen.saturating_add(1);
        let vol = match ctx.read_indicator(&REALIZED_VOL_INDICATOR.to_string()) {
            Ok(v) => v,
            Err(_) => {
                ctx.log_signal("vol_value", 0.0, false, Some("indicator_warmup"));
                return Ok(());
            }
        };
        ctx.log_signal("vol_value", vol, true, None);

        let pos = ctx.get_position(&self.params.symbol);
        let is_short = pos.size < 0.0;
        if !is_short && vol <= self.params.vol_lo {
            ctx.log_signal("enter_short", vol, true, None);
            ctx.submit_order(
                &self.params.symbol, Side::Short, self.params.size,
                None, None, Some("contango_low_vol_entry"),
            )?;
            return Ok(());
        }
        if is_short && vol >= self.params.vol_hi {
            ctx.log_signal("exit_short", vol, true, None);
            ctx.submit_order(
                &self.params.symbol, Side::Long, self.params.size,
                None, None, Some("backwardation_exit"),
            )?;
            return Ok(());
        }
        ctx.log_signal("hold", vol, false, Some("threshold_band"));
        Ok(())
    }

    fn on_fill(&mut self, _fill: &Fill, _ctx: &mut dyn Context) -> Result<()> {
        Ok(())
    }

    fn on_end(&mut self, ctx: &mut dyn Context) -> Result<()> {
        ctx.log_decision("end", json!({ "warmup_bars_seen": self.warmup_bars_seen }));
        Ok(())
    }
}

fn factory() -> Box<dyn Strategy> { Box::<VxxStrategy>::default() }

strategy_entry!(factory);
```

---

## 10. Engine indicator registry

The engine ships a fixed set of indicators. The prompt builder embeds the
active list under this section at call time. If an indicator name you want is
not listed, the strategy must compute it from `Bar` history itself (carry the
sliding window on `self`).

(Generated at prompt-build time. The placeholder rendering during tests is
`<indicators>` — replaced by the actual registry.)

---

## 11. Tooling expectations the build pipeline enforces

- `cargo fmt --check` — code must be rustfmt-clean.
- `cargo clippy --all-targets -- -D warnings` — clippy-clean.
- `cargo build` against the workspace's pinned toolchain (`rust-toolchain.toml`).
- Allowed-crate check against the whitelist in §6.
- Source linter — no constructs from §8.

All of these run before the artifact is cached. A clippy warning or rustfmt
mismatch hard-rejects the candidate with the offending diagnostic surfaced to
the repair loop.

---

## 12. Versioning

The engine-rt ABI is **not** versioned for hypothesize artifacts. Strategies
emitted today may fail to recompile against a future engine-rt; that is
accepted limitation. Replay against the matching engine-rt revision (recorded
in git history alongside the source blobs) is the supported workflow when
exact reproduction is needed across breaking changes.
