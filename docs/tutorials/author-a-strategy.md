# Author a strategy

## Learning goal

Drive `strategy-gpt author` end-to-end against a small natural-language seed. By the end you will have a compiled, smoke-tested strategy crate on disk under `crates/<name>-strategy/`, an `intent.toml` record you can round-trip from Python, and the next-step command for handing the strategy off to the hypothesize loop.

## Prerequisites

- A working clone of `strategy-gpt`.
- A Rust toolchain matching `rust-toolchain.toml` (1.82.0).
- Python 3.11+ with `pip` and a venv.
- `maturin` (installed automatically by `pip install -e 'python/[dev]'`).
- An `ANTHROPIC_API_KEY` **or** `OPENAI_API_KEY` exported in the environment. The author command picks the most capable model whose key is present; both keys may be set.
- First-time network access to `yfinance` so the smoke backtest can fetch bars. Subsequent runs are offline-capable from the cache.

!!! note "Dialog non-determinism"
    Author drives an interactive LLM dialog. The exact clarifying questions and the wording of the LLM's responses will vary across runs; the load-bearing outcome is the *shape* of the on-disk artifacts the run produces, not a byte-for-byte transcript match. The dialog excerpts below are illustrative.

## Walkthrough

### 1. Install the Python orchestrator and native bindings

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e 'python/[dev]'
maturin develop -m crates/py-bindings/Cargo.toml
export ANTHROPIC_API_KEY="..."   # or OPENAI_API_KEY
```

Expected: a series of build lines ending with

```
📦 Built wheel for CPython 3.11 ...
🛠 Installed strategy-gpt-...
```

### 2. Invoke `author` with a natural-language seed

```bash
strategy-gpt author "long-only SMA crossover on SPY with a 50/200 default, daily bars"
```

The LLM opens the dialog with the seed as initial context. It will ask one focused clarifying question per turn until it has enough to commit to an `AuthorIntent` block. Typical questions the dialog will surface (in roughly this order):

```
What window do you want the smoke backtest to cover?
> 2022-01-01 to 2023-06-01

Should the strategy size positions as a fixed share count or a fixed notional?
> Fixed notional of 10000.

Any guardrails I should respect (max position, no-shorts, etc.)?
> Long-only, single position at a time.
```

Once the LLM has answered the basics it emits a `# AuthorIntent` section and the command proceeds to the emit / build / smoke stage automatically.

> **Look for the locked-in decisions panel.** After every operator answer you should see a banner like:
>
> ```
> ────────────────────────────────────────────────────────────────
> Decisions locked in so far
> ────────────────────────────────────────────────────────────────
>   name      spy-sma-crossover
>   universe  SPY
>   smoke     symbol=SPY, start=2022-01-01, end=2023-06-01
> ────────────────────────────────────────────────────────────────
> ```
>
> The panel is the authoritative state of the dialog — it is projected from `crates/<name>-strategy/.author/decisions.jsonl`, not from the LLM's chat history. So even if the conversation gets long enough that the model has to compact its context, the panel keeps showing the same facts. The LLM's exact wording for clarifying questions will vary across runs; the load-bearing outcome is the *contents* of this panel and the on-disk crate at the end. Pass `--quiet` to hide the panel if you don't want to see it.

### 3. Watch the emit / build / smoke loop run

The command writes the proposed files into `crates/spy-sma-crossover-strategy/` on every attempt, runs the build pipeline (lint + `cargo build`), and on a successful build runs a smoke backtest. The terminal output reads roughly like:

```
[author] Writing crates/spy-sma-crossover-strategy/{Cargo.toml, src/lib.rs, smoke.toml}
[author] cargo build -p spy-sma-crossover-strategy ... OK (3.2s)
[author] smoke run on SPY 2022-01-01..2023-06-01 ... OK (12 trades)
```

On failure, the LLM gets the diagnostic and tries again, up to the configured `--k-repair-emit` / `--k-repair-build` budgets (default `k_repair=2`, so three total attempts). When a budget is exhausted, control returns to the dialog so you can adjust the intent (e.g. expand the smoke window) and retry.

### 4. Inspect the on-disk artifacts

```bash
ls crates/spy-sma-crossover-strategy/
```

Expected:

```
Cargo.toml
intent.toml
smoke.toml
src/
```

```bash
cat crates/spy-sma-crossover-strategy/smoke.toml
```

Expected (the values reflect the smoke window the dialog agreed on):

```toml
symbol = "SPY"
resolution = "1d"
start = "2022-01-01"
end = "2023-06-01"
provider = "yfinance"
```

```bash
head -20 crates/spy-sma-crossover-strategy/src/lib.rs
```

Expected (the LLM's exact code will differ; the shape of the file is what matters):

```rust
use engine_rt::{
    strategy_entry, Bar, Context, Fill, Result, Sealed, Side,
    Strategy, StrategyMeta,
};

#[derive(Default)]
pub struct SpySmaCrossover {
    closes: Vec<f64>,
}

impl Sealed for SpySmaCrossover {}

impl Strategy for SpySmaCrossover {
    // ...
}

strategy_entry!(SpySmaCrossover);
```

### 5. Round-trip `intent.toml` from Python

The intent record is the durable handle on what the LLM committed to. Load it back:

```bash
python -c "
from pathlib import Path
from strategy_gpt.author import load_intent_toml

intent = load_intent_toml(Path('crates/spy-sma-crossover-strategy'))
print(intent.name)
print(intent.mechanism_summary)
print(intent.smoke_spec.symbol, intent.smoke_spec.start, '→', intent.smoke_spec.end)
"
```

Expected (substance reflects the dialog):

```
spy-sma-crossover
Goes long SPY when the 50-day SMA crosses above the 200-day SMA;
exits flat on the opposite crossover. Position sizing is fixed notional.
SPY 2022-01-01 → 2023-06-01
```

The same record is what the hypothesize loop will read when you hand the strategy off downstream.

### 6. Hand the strategy off to the hypothesize loop

```bash
strategy-gpt hypothesize spy-sma-crossover --dry-run
```

Expected: a JSON envelope confirming the resolved strategy name, ledger root, and the orchestrator wiring's `wiring_incomplete` status. The dry-run validates the strategy is discoverable from the workspace without running the full loop.

## What you just did

You drove the author command from a one-line NL seed to a working `cdylib`. Author elicited the missing detail through a dialog, emitted `Cargo.toml` + `src/lib.rs` + `smoke.toml` under `crates/spy-sma-crossover-strategy/`, ran the lint + `cargo build` pipeline, fetched SPY bars through the data gateway, ran a smoke backtest, and persisted an `intent.toml` record alongside the source. There was no ledger row, no falsification check, and no verdict — author's only success bar is "compiles and smoke passes." The crate is now in the same shape every other surface (`strategy-gpt run`, `hypothesize`, `optimize`) consumes.

## What next

- **How-to** — [Author a strategy](../how-to/author-a-strategy.md): the task-oriented recipe collection, including `--verify=batch`, repair-budget tuning, and edit-mode against an existing crate.
- **How-to** — [CLI cookbook → Author a strategy](../how-to/cli-cookbook.md): the cookbook recipes for the same command in the company of `fetch`, `run`, `hypothesize`, and `optimize`.
- **Explanation** — [Hand-authoring a strategy](../explanation/hand-authoring-a-strategy.md): the engineer-targeted deep dive on the sealed `Strategy` trait surface that author targets. Read this when extending the trait, debugging an author emission, or contributing to the engine.
- **Tutorial** — [Walking the hypothesize loop](hypothesize-loop.md): drive the hypothesis loop's `hypothesize`, `hypothesis replay`, and `hypothesis diff` surface against the strategy you just authored (or a stub ledger fixture).
- **Reference** — [`crates/engine-rt/PROMPT_API.md`](https://github.com/AlphaGit/strategy-gpt/blob/main/crates/engine-rt/PROMPT_API.md): the authoritative lifecycle + `Context` surface the LLM is targeting.
