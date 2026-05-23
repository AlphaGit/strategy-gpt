## Why

The `strategy-gpt author` dialog runs as an opaque conversation: locked-in decisions live only in the LLM's message history, the operator gets no in-band feedback while the emit/build/smoke loop runs, and a repair-budget exhaustion ends the run with a stack-shaped error instead of handing control back. Three pain points fall out: (1) long dialogs are compaction-fragile — if context is trimmed mid-session the prior decisions are silently lost; (2) the operator cannot tell what the strategy looks like so far without scrolling the transcript; (3) when the repair loop fails, the operator's only recourse is to re-launch from scratch even though their accumulated intent is still valid.

## What Changes

- Add a structured **DecisionRecord** that the dialog writes to, separately from the LLM's free-form conversation. Each accepted clarification (universe, mechanism, parameter sketch, smoke fixture, crate name, edit-mode flag, etc.) lands as a typed entry in this record, persisted to `crates/<name>-strategy/.author/decisions.jsonl` while the dialog is in flight. The record is the authoritative source the LLM resumes from after any compaction event; the conversational history becomes a non-load-bearing scratchpad.
- Render a **locked-in decisions panel** in the dialog UI between turns. After every accepted clarification, the CLI prints a compact summary of every decision so far (name, mechanism summary, parameter sketch, smoke spec, etc.) so the operator can see the strategy taking shape in real time. The panel is rendered from the DecisionRecord, not from the LLM output.
- Emit **operation feedback events** during the emit / build / smoke loop. Today the loop is silent until it either finishes or raises. Going forward, the CLI surfaces a structured stream of progress messages: which file is being written, which `cargo` command is running, which provider is being hit for smoke bars, smoke run start/end, sanity-trip counts, etc. Verbosity is controllable (default: human-readable progress; `--quiet` collapses to a one-line spinner; `--verbose` includes underlying command lines).
- Convert **repair-budget exhaustion into a control transfer** rather than a terminal error. When `k_repair_emit` or `k_repair_build` runs out, the dialog regains control with the failure trail summarized as a first-class dialog turn. The operator can: (a) propose an alternative approach in natural language (which the LLM uses to amend the intent), (b) extend the budget and retry, (c) edit the intent's parameter sketch / mechanism directly, or (d) abort. The crate files on disk are left intact in either case so they can be inspected.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities

- `author`: add requirements for the structured DecisionRecord (compaction-resilient resumption), locked-in decisions UI panel, structured operation-feedback event stream during emit/build/smoke, and control-transfer-on-repair-exhaustion with operator action menu. Touches existing requirements `Interactive intent dialog`, `Emit / build / smoke repair loop`, and the repair-budget-exhausted scenario under that requirement.

## Impact

- Modified file: `python/strategy_gpt/author.py` — new `DecisionRecord` dataclass and JSONL persistence, dialog turn loop writes to the record on every accepted clarification, repair-loop catch site replaced with a re-entrant dialog turn.
- Modified file: `python/strategy_gpt/cli.py` — render locked-in decisions panel between turns, surface progress events as human-readable lines, add `--quiet` / `--verbose` flags.
- Modified file: `python/strategy_gpt/prompts_author.py` — prompts read from DecisionRecord rather than reconstructing intent from message history; system prompt instructs the LLM that the decision log, not its chat history, is the authoritative state.
- New file (per run): `crates/<name>-strategy/.author/decisions.jsonl` — typed, append-only record. Survives the run as a debugging / replay artifact.
- New tests under `python/tests/`: DecisionRecord round-trip + resumption-after-compaction; locked-in panel rendering; progress-event stream coverage; repair-exhaustion control transfer happy path and operator-driven retry.
- No new external dependencies. No API changes to `author_strategy` library seam (the function signature stays; behavior under the hood becomes resumable).
- Backwards-compatible at the CLI surface: existing invocations produce the same on-disk crate; the difference is what the operator sees during the run and what happens on failure.
