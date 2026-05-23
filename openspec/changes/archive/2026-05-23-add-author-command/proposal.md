## Why

The expected research flow is `author → backtest (optional) → hypothesize → optimize`, but the loop currently has no root primitive for *creating* a strategy from human intent. Today the only path that emits Rust strategy code is the hypothesis loop's `generate` stage, which presupposes a baseline strategy plus a diagnosis — there is no entry point a human can use to say "write me a trend-follow SPY strategy with ATR stops" and end up with a compiled, smoke-tested crate on disk. The hypothesis loop, optimizer, and tester all assume a working strategy exists; until `author` lands, that strategy must be hand-written, which is the one thing the platform exists to automate.

## What Changes

- Add a new top-level CLI command, `strategy-gpt author ["initial idea"] [--verify=batch] [--k-repair-emit=N --k-repair-build=N]`, that drives an interactive LLM dialog and emits a working strategy crate under `crates/<name>-strategy/`.
- Add a new Python module `python/strategy_gpt/author.py` exposing `author_strategy(intent: AuthorIntent, *, deps: AuthorDeps) -> AuthoredStrategy` as the library seam. The CLI is a thin Typer wrapper around it. Future work (out of scope for this change) collapses `hypothesis_loop.generate` into a call to this library.
- The author flow has two stages:
  1. **Dialog stage** — LLM accepts the optional NL seed, asks clarifying questions about universe / mechanism / params / smoke window, and produces a structured `AuthorIntent` (name, NL description, mechanism summary, param schema sketch, smoke spec, optional full-batch spec). Auto-detects edit-vs-new: if the proposed crate name collides with an existing `crates/<name>-strategy/`, the dialog asks the user whether to edit or rename, and on edit loads the existing `intent.toml`, `src/lib.rs`, `Cargo.toml`, and `smoke.toml` into the LLM context.
  2. **Emit / build / smoke stage** — LLM emits `src/lib.rs`, `Cargo.toml`, `smoke.toml` (and `experiment.yaml` when `--verify=batch`) directly into `crates/<name>-strategy/` on every attempt. Author runs the existing `build_pipeline` (lint, allowed-crate whitelist, `cargo build -p <name>-strategy`) then a smoke backtest against the fixture declared in `smoke.toml` using the existing `data-gateway`. Failures feed back into a repair loop with a configurable per-stage budget (default `k_repair=2`). When the budget is exhausted, the dialog resumes and asks the user for guidance instead of failing the command.
- The author flow has no falsification check, no ledger record, and no reject-taxonomy verdict. Success is "the crate compiles and smoke passes." The on-disk `crates/<name>-strategy/` is itself the durable record; `intent.toml` is persisted alongside it.
- Few-shot exemplars in every prompt: the existing `crates/vxx-strategy/` and `crates/example-strategy/` are always loaded so the LLM has a complete reference for the sealed `Strategy` trait, `ParamSchema`, `Context` surface, and manifest shape.
- Crates outside the build-pipeline whitelist are a hard reject. Author does not request additions; the LLM receives the whitelist in its prompt and is expected to honor it. A manifest with a non-whitelisted dep aborts the build with a feedback string the repair loop can iterate on.
- One-time workspace refactor: `crates/Cargo.toml` switches from an explicit `members = [...]` list to `members = ["*"]` so author never has to mutate the workspace manifest when creating a new crate.

## Capabilities

### New Capabilities

- `author` — interactive LLM-driven creation and editing of strategy crates. Specs the dialog contract, the emit/build/smoke loop, the edit-mode trigger, the on-disk artifact set, and the hard-reject-on-non-whitelisted-crate rule.

### Modified Capabilities

(none — the build-pipeline behavior author depends on is already in place; the author capability spec documents package-scoped invocation in its own requirements.)

## Impact

- New CLI: `strategy-gpt author` in `python/strategy_gpt/cli.py`.
- New module: `python/strategy_gpt/author.py` (library seam + dialog driver).
- New prompt scaffolds: `author`-specific stage prompts in `python/strategy_gpt/prompts.py` (or a new `prompts_author.py`) — distinct from `build_stage1_prompt` / stage2 because there is no diagnosis, no baseline metric set, and no falsification.
- New artifact contract: `crates/<name>-strategy/intent.toml` and `crates/<name>-strategy/smoke.toml` schemas (and the optional `experiment.yaml` schema when `--verify=batch`).
- Modified file: `crates/Cargo.toml` (workspace `members = ["*"]` one-time refactor).
- New tests under `python/tests/test_author.py` covering: dialog → intent normalization; edit-mode auto-detection on name collision; emit/build/smoke with stubbed LLM + real build pipeline against `example-strategy`-shaped output; hard-reject on non-whitelisted crate; per-stage repair budget exhaustion handing control back to dialog.
- Reuses without changing: `build_pipeline.BuildPipeline`, `data-gateway` fetch+cache for smoke bars, `repair.run_stage_with_repair` + `RepairConfig`, `validation.validate_stage3` (where applicable for source+manifest parse), `reasoning_clients` for LLM dispatch.
- Out of scope: ledger integration, falsification, reject taxonomy, optimization wiring, collapsing `hypothesis_loop.generate` into `author_strategy` (planned follow-up, not in this change).
