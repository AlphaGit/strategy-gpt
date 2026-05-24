## Context

`strategy-gpt author` today is built around a single `run_intent_dialog` function that drives a chat-shaped LLM session: the operator types, the LLM replies, eventually an `AuthorIntent` dataclass is emitted, and `author_strategy` takes over. The intent is reconstructed from the chat history on every turn — there is no separately-persisted record of what has been decided. The emit/build/smoke loop in `author_strategy` likewise runs as one tight Python function: it writes files, shells out to `cargo`, runs the smoke engine, and returns success or raises. Nothing about progress is surfaced to the CLI between these calls.

This shape was fine for the smoke build of the feature but does not hold up under three real-usage pressures:

1. **Long dialogs hit context limits.** A dialog with non-trivial back-and-forth (e.g. ten clarifying questions before mechanism alignment) can exceed cache windows or model context. When the LLM compacts or loses the head of history, prior decisions silently regress and the dialog re-asks questions it has already settled.
2. **The operator has no in-flight view of the strategy.** The only way to see what's been decided so far is to scroll the chat. The "what is this strategy?" answer lives implicitly in the LLM's working memory.
3. **Repair-budget exhaustion is a dead end.** When the build/smoke loop burns through `k_repair_emit` attempts, the current code raises and the operator must re-launch from scratch, losing the dialog state.

This change introduces a structured record of state, a UI panel that renders that record, an event stream during code-emission, and a control-transfer path on repair exhaustion. None of these change the on-disk crate shape; they all change what the operator sees and what survives compaction.

## Goals / Non-Goals

**Goals:**
- DecisionRecord is the authoritative source of dialog state; LLM chat history is a non-load-bearing scratchpad.
- A compaction event mid-dialog (whether driven by token budget, an explicit reset, or a session resume in a new process) does not lose any locked-in decision.
- The operator can, at any point in the dialog, see a rendered summary of every locked-in decision without scrolling.
- During emit/build/smoke, the operator gets a structured stream of progress messages identifying which substep is running and on what input.
- Repair-budget exhaustion hands control back to the dialog with the failure trail summarized, and the operator can amend, retry-with-more-budget, or abort.
- `author_strategy` library seam preserves its current signature; programmatic callers see no behavior change other than the side-channel event stream (which they can ignore).

**Non-Goals:**
- No persistent session resume across process restarts beyond what `.author/decisions.jsonl` enables in-place (i.e., we do not build a "session recovery" CLI command in this change).
- No change to the success bar or to the crate artifact set.
- No change to the LLM prompt structure beyond what the DecisionRecord requires — we are not reworking the prompts file in this pass.
- No new model selection or budget heuristics; the operator still picks the model and the budgets at the CLI.

## Decisions

### Decision: DecisionRecord lives on disk, not just in memory

Persisted as `crates/<name>-strategy/.author/decisions.jsonl` from the first accepted decision onward (typically the crate name itself, which is what determines the directory). One JSON object per line, append-only, schema-versioned.

**Why on-disk:** the LLM can read the file back on resume; the operator can inspect it; tests can diff against expected records. An in-memory-only record loses everything on a process restart.

**Why JSONL not TOML/YAML:** append-only is the natural shape (each decision lands as one event). A multi-document JSONL is also trivial to load without parsing a tree.

**Why under `.author/` and not at the crate root:** `intent.toml` and friends are the *output* of the author run; the decision log is a working artifact. Keeping it under a hidden subdirectory keeps the crate root clean and signals "this is author-machinery, not strategy code."

**Alternative considered:** persist to `ledger/` like all other system state. Rejected — the ledger is for run-level reproducibility, not for in-flight dialog state. Coupling the dialog UI to ledger writes also makes testing harder.

### Decision: DecisionRecord schema is typed-event, not a flat dict

Each entry is an event with `event_type`, `timestamp`, and a typed payload. Event types in scope for this change:

- `dialog_started` — opens the record; carries the optional NL seed and the LLM model name.
- `decision_locked` — a clarification was accepted by the operator. Carries `field` (`crate_name`, `universe`, `mechanism_summary`, `param_sketch`, `smoke_spec`, `experiment_spec`, `edit_mode_target`, …) and `value` (typed per field).
- `decision_amended` — operator changed their mind about a prior decision. Carries `field`, `old_value`, `new_value`. The locked-in panel always renders the most recent value per field.
- `intent_finalized` — the dialog hands the assembled `AuthorIntent` to `author_strategy`. Carries the serialized intent.
- `repair_budget_exhausted` — control transferred back to the dialog after emit/build/smoke ran out of attempts. Carries the failure trail summary.

**Why typed events instead of a flat `{field: value}` dict:** amendments and re-entries from repair exhaustion are common in practice. A flat dict would require either lossy overwrites or out-of-band history tracking. Events keep the full history without complicating the locked-in panel — the panel computes "current value per field" by replaying events in order, last-write-wins.

**Alternative considered:** persist the canonical `AuthorIntent` dataclass instance as it grows. Rejected — `AuthorIntent` is the *output* of the dialog; constructing partial intents during the dialog leaks dialog-stage state into the post-dialog data type.

### Decision: Locked-in panel renders synchronously between turns

Between each LLM turn, the CLI prints a banner-style panel summarizing the current state of the DecisionRecord. The panel is built by replaying events into a `{field: current_value}` projection and pretty-printing the result. Format is compact (≤15 lines for a typical mid-dialog state), uses fixed-width labels (`name:`, `mechanism:`, `params:`, etc.), and is bracketed by visual separators so it is distinguishable from LLM output.

**Why synchronous render, not a long-lived TUI:** a TUI library (e.g. `textual`, `rich.Live`) adds an external dependency and complicates the test surface. The operator workflow is turn-based; synchronous render at turn boundaries gives the same value at a fraction of the implementation cost.

**Alternative considered:** render the panel only on operator request (e.g. `/decisions` slash command in the dialog). Rejected — the whole point is to make the strategy state visible without the operator having to ask.

### Decision: Operation feedback is a structured event stream, not just stdout strings

`author_strategy` (and the helpers it calls) takes an optional `event_sink: Callable[[AuthorEvent], None]` parameter. Each substep — file write, lint, cargo invocation start, cargo invocation end, smoke fetch, smoke run, sanity-trip count — emits an `AuthorEvent` to the sink. The CLI provides a sink that renders events as human-readable lines; tests can provide a sink that collects events into a list for assertions; programmatic callers can pass `lambda _: None` (or omit the parameter, default is a no-op sink).

**Why an event sink and not direct prints:** direct prints bind the library to a CLI presentation. The event-sink shape lets the CLI control verbosity (`--quiet`, `--verbose`) without the library knowing, and lets future callers (the hypothesis loop) consume the same events programmatically.

**Event types in scope:** `file_written(path)`, `lint_started`, `lint_completed(result)`, `cargo_build_started(args)`, `cargo_build_completed(returncode, duration)`, `smoke_fetch_started(symbols, range)`, `smoke_fetch_completed`, `smoke_run_started`, `smoke_run_completed(trade_count, sanity_trips)`, `repair_attempt_started(attempt, budget)`, `repair_attempt_completed(outcome)`.

**Alternative considered:** Python `logging` with a custom handler. Rejected — `logging` is global state, hard to scope per-author-run, and the level→event-shape impedance mismatch creates noise. A typed event sink is a small amount of code and a much cleaner contract.

### Decision: Repair-budget exhaustion is a recoverable dialog turn, not an exception

When the repair loop runs out of attempts, `author_strategy` does not raise. Instead, it returns a `RepairExhausted` sentinel (or analogous typed result) carrying the failure trail. The CLI translates this into a new dialog turn: the LLM is given the failure summary and asked to propose options to the operator. The operator picks from a menu:

1. **Suggest alternative approach** — operator types free-form text; LLM amends the intent (writes `decision_amended` events), the emit/build/smoke loop restarts with fresh budget.
2. **Retry with extended budget** — operator specifies new `k_repair_emit` / `k_repair_build` values; the loop restarts with the existing intent.
3. **Edit a specific decision** — operator names a field (e.g. `param_sketch`), the LLM walks them through revising just that field, the loop restarts.
4. **Abort** — the run exits non-zero with the crate files left on disk for inspection.

**Why a menu and not free-form-only:** the menu makes the operator's options legible. Free-form responses still work (they map to option 1), but operators who don't know what to suggest get a discoverable next step.

**Why crate files left on disk regardless:** the operator may want to inspect what the LLM tried and patch it by hand. This also matches the existing `--verify=batch` failure behavior.

**Alternative considered:** auto-retry with widened budget once and only raise on second exhaustion. Rejected — it hides the failure, costs budget without operator consent, and does nothing to help if the failure mode is "wrong mechanism" rather than "needs more tries."

### Decision: Library seam stays signature-compatible

`author_strategy(intent: AuthorIntent, *, deps: AuthorDeps) -> AuthoredStrategy` keeps its current signature. The event sink is added to `AuthorDeps` (with a no-op default), and the repair-exhausted result is reachable only through the dialog wrapper, not the library seam — programmatic callers continue to get an exception on exhaustion since they have no dialog to re-enter.

**Why this asymmetry:** the dialog wrapper has somewhere to send control on exhaustion; the library seam does not. Making the seam return-shape mirror the dialog wrapper's would force every programmatic caller to handle a result type they have no recovery path for.

## Risks / Trade-offs

- **Risk:** The DecisionRecord and the LLM's free-form chat history drift apart — the LLM "forgets" a decision that the record still holds, and the next turn re-asks. → **Mitigation:** every prompt assembles the LLM-visible state from the DecisionRecord (system-prompt section: "decisions so far: …"), so the chat history is decorative and the record is authoritative. Tests assert that re-asking a settled question is a regression.
- **Risk:** The locked-in panel becomes noise as the strategy state grows. → **Mitigation:** cap the panel at one screen height (≈15 lines), collapse long-form fields (e.g. `mechanism_summary`) to a head + ellipsis, and surface the full field on a follow-up dialog turn if the operator asks. `--quiet` suppresses the panel entirely.
- **Risk:** The event stream lands too verbose at default verbosity and the operator can't see the LLM dialog through the cargo spam. → **Mitigation:** default verbosity surfaces *transitions* only (started/completed pairs collapse to "running … done in 4.2s"), and per-line cargo/rustc output is gated behind `--verbose`. The CLI groups events visually so the dialog frame stays distinguishable.
- **Risk:** Operators get used to the "suggest alternative approach" path and use it as a substitute for thinking up-front, eating LLM budget. → **Mitigation:** this is partly a feature (the loop is supposed to be cheap to iterate on), but the repair-exhausted menu surfaces the failure trail prominently so operators can see the cost of each retry. No additional gate in this change.
- **Trade-off:** The hidden `.author/` directory pollutes a "clean" crate layout. → Accepted — the directory is hidden, gitignored at the workspace level (we will add `crates/*-strategy/.author/` to `.gitignore` as part of this change), and only present during/after an author run. Engineers reading the crate cold won't see it.

## Migration Plan

This is an additive UX change with no migration burden:

- Existing author runs in flight (rare; sessions are short) are unaffected — the new code is only on the trunk going forward.
- Existing crates on disk have no `.author/` directory and never will, unless `strategy-gpt author` is re-run against them. Edit-mode does not retroactively create one; if it re-enters and accepts decisions, it starts a fresh `decisions.jsonl`.
- No spec deprecations. The `Repair budget exhausted` scenario under `Emit / build / smoke repair loop` is *modified* (not removed) to reflect the new control-transfer behavior.

## Open Questions

- **Question:** Should the event sink also receive *dialog-stage* events (`decision_locked`, etc.) so a single sink can render the whole run, or should we keep dialog events on the DecisionRecord only? → Tentative answer: dialog events stay on the DecisionRecord; the sink is for emit/build/smoke. Mixing them complicates the CLI's rendering logic and there's no consumer for a unified stream today. Revisit when the hypothesis loop wires up its `generate` stage to `author_strategy`.
- **Question:** Do we want a `--no-decisions-panel` flag separate from `--quiet`, or is `--quiet` enough? → Tentative answer: `--quiet` is enough. The panel is the single most useful in-flight UI element; if the operator wants to silence it, they probably want to silence everything else too.
