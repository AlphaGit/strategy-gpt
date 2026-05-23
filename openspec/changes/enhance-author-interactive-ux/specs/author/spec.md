## ADDED Requirements

### Requirement: Structured DecisionRecord is the authoritative dialog state

The Author SHALL maintain a structured, on-disk DecisionRecord at `crates/<name>-strategy/.author/decisions.jsonl` that is the authoritative source of every accepted clarification during the dialog. Each accepted decision MUST be appended as a typed event (`dialog_started`, `decision_locked`, `decision_amended`, `intent_finalized`, `repair_budget_exhausted`). The LLM's free-form chat history MUST NOT be the source of truth for dialog state; every prompt SHALL assemble the LLM-visible decision state from the DecisionRecord so that a chat-history compaction event does not lose any locked-in decision.

#### Scenario: Each accepted clarification is appended as a typed event

- **WHEN** the dialog accepts a clarification (e.g. operator confirms the proposed `crate_name` is `spy-atr`)
- **THEN** a `decision_locked` event with `field="crate_name"` and `value="spy-atr"` is appended to `crates/spy-atr-strategy/.author/decisions.jsonl` before the next LLM turn is dispatched

#### Scenario: Compaction-resilient resumption

- **WHEN** the dialog has accepted three decisions (`crate_name`, `universe`, `mechanism_summary`) and the LLM's chat history is then compacted (head trimmed)
- **THEN** the next prompt assembled by the Author rehydrates the three locked-in decisions from `.author/decisions.jsonl` into the system prompt and the LLM does not re-ask any of them

#### Scenario: Amendments preserve history

- **WHEN** the operator changes their mind about the `param_sketch` after it was previously locked in
- **THEN** a `decision_amended` event is appended carrying both `old_value` and `new_value`, the original `decision_locked` event remains in the file, and the locked-in panel renders the new value

### Requirement: Locked-in decisions panel renders between dialog turns

The Author SHALL render a locked-in decisions panel between every LLM dialog turn. The panel MUST be projected from the DecisionRecord by replaying events in order (last-write-wins per field) and MUST render the current value of every locked field with a fixed-width label and a visual separator distinguishing it from LLM output. The panel MUST fit within roughly one screen (collapse long-form fields to a head + ellipsis if needed). `--quiet` SHALL suppress the panel; otherwise it is rendered by default.

#### Scenario: Panel appears between turns

- **WHEN** the operator answers a clarification and the dialog is about to dispatch the next LLM turn
- **THEN** the CLI prints a panel containing every currently-locked field's label and value, bracketed by separator lines, before the LLM's next message is streamed

#### Scenario: Panel reflects the most recent amendment

- **WHEN** the operator amends a previously-locked decision and the next turn is dispatched
- **THEN** the panel renders the new value, not the original

#### Scenario: --quiet suppresses the panel

- **WHEN** the operator invokes `strategy-gpt author --quiet "..."`
- **THEN** no decision panel is rendered between turns; the DecisionRecord on disk is unaffected

### Requirement: Structured operation-feedback event stream during emit/build/smoke

The Author SHALL emit structured progress events to a `event_sink` callable for every substep of the emit/build/smoke loop: file writes, lint start/end, cargo invocation start/end (with returncode and duration on end), smoke data fetch start/end, smoke run start/end (with trade count and sanity-trip count on end), and per-repair-attempt start/end (with attempt index and budget). The `event_sink` MUST be injectable via `AuthorDeps` and MUST default to a no-op so programmatic callers (e.g. the hypothesis loop's `generate` stage) need not consume the stream. The CLI MUST install a sink that renders events as human-readable progress lines; `--quiet` collapses these to a one-line spinner, `--verbose` includes per-line cargo and rustc output.

#### Scenario: Default verbosity surfaces transitions

- **WHEN** the CLI runs an author session and `cargo build` completes
- **THEN** the operator sees a single human-readable line like `cargo build -p spy-atr-strategy ... done in 4.2s` rather than the per-line cargo stream

#### Scenario: --verbose surfaces underlying commands

- **WHEN** the CLI runs an author session with `--verbose` and `cargo build` runs
- **THEN** the operator sees the full cargo/rustc stream interleaved with the structured progress lines

#### Scenario: Programmatic caller passes a custom sink

- **WHEN** a test calls `author_strategy(intent, deps=replace(deps, event_sink=collected.append))`
- **THEN** every emitted `AuthorEvent` is appended to `collected` and no event is printed to stdout

#### Scenario: Default sink is a no-op

- **WHEN** a programmatic caller invokes `author_strategy(intent, deps=deps)` without overriding `event_sink`
- **THEN** the call runs the full emit/build/smoke loop without writing any feedback to stdout or stderr

### Requirement: Repair prompt carries diagnostic AND previous emission

Every repair attempt's prompt SHALL contain BOTH the validator's failure diagnostic (rustc stderr, lint rejection summary, whitelist offender, smoke panic message, or zero-trade signal) AND the verbatim text of the LLM's previous failed emission, rendered under a "Your previous attempt (revise this; do not start from scratch)" section. The prompt MUST instruct the LLM to preserve unaffected parts of the previous emission and target the change to what the diagnostic identifies. The `emit_files` API surface is single-turn (no conversation continuity), so the previous emission must be re-supplied in-prompt; relying on chat history is not acceptable.

#### Scenario: Build fails, repair prompt includes diagnostic and previous emission

- **WHEN** the first emission fails `cargo build` with a borrow-checker error and the repair loop dispatches a second attempt
- **THEN** the second emit-stage user prompt contains the rustc diagnostic under a "Why the previous attempt was rejected" section AND the full text of the first emission under "Your previous attempt (revise this; do not start from scratch)"

#### Scenario: Non-whitelisted dep surfaces in repair prompt

- **WHEN** the first emission declares a non-whitelisted crate and the build pipeline rejects it
- **THEN** the next attempt's prompt names the offending crate and the whitelist rule in the feedback section, the previous emission is rendered verbatim above it, and the LLM is expected to drop or substitute the dependency rather than re-derive the whole crate

### Requirement: Operator input supports multi-line answers

The author dialog SHALL accept multi-line operator answers through two mechanisms: (1) a typed sentinel mode in which a line containing only `<<<` opens a multi-line block and a line containing only `>>>` closes it, with all intervening lines preserved verbatim (joined by `\n`); and (2) a paste mode in which the CLI's `input` wrapper probes stdin for buffered lines after each line read and concatenates them with `\n` so a multi-line paste arrives as a single reply. Single-line typing MUST remain the default — neither mode requires the operator to opt in for short answers. The same input surface SHALL be used for both clarifying-question turns in the dialog and for free-form guidance prompts in the repair-exhaustion menu.

#### Scenario: Sentinel-mode multi-line typing

- **WHEN** the operator types `<<<` on its own line, then several lines of content, then `>>>` on its own line
- **THEN** the dialog accepts the joined content (with internal newlines preserved) as one operator answer; the `<<<` and `>>>` markers are stripped

#### Scenario: Paste-mode multi-line input

- **WHEN** the operator pastes a multi-line block into the terminal and presses Enter once
- **THEN** the CLI input wrapper consumes the first line, probes stdin within a short window for additional buffered lines, and returns the concatenated block as a single reply

#### Scenario: Single-line input is unchanged

- **WHEN** the operator types a short single-line answer and presses Enter
- **THEN** the dialog returns that line verbatim with no probing prompt and no paste-join behavior

### Requirement: Cargo build progress ticks during long builds

While `cargo build` is in flight, the Author SHALL emit `CargoBuildProgress` events to the event sink at regular intervals (default: every 2 seconds) so the CLI can surface in-flight progress to the operator. Each tick carries the elapsed seconds since the build started. The watcher MUST stop the moment the build returns (success or failure), so a fast or stubbed build emits no ticks. Tick emission MUST run concurrently with the build (e.g. on a daemon thread); it MUST NOT block on the build.

#### Scenario: Long build emits intermediate progress events

- **WHEN** the build pipeline blocks for several seconds before returning (typical for a real `cargo build`)
- **THEN** the event sink receives one or more `CargoBuildProgress` events between `CargoBuildStarted` and `CargoBuildCompleted`, each carrying a strictly-increasing `elapsed_seconds`

#### Scenario: Instant build emits no progress ticks

- **WHEN** the build pipeline returns immediately (e.g. test stub, cache hit)
- **THEN** the event sink receives `CargoBuildStarted` followed by `CargoBuildCompleted` with no intervening `CargoBuildProgress` event

## MODIFIED Requirements

### Requirement: Interactive intent dialog

The Author SHALL accept an optional natural-language seed and drive an interactive LLM dialog that produces a structured `AuthorIntent` before any code is emitted. The dialog MUST ask clarifying questions about universe, mechanism, parameter sketch, and smoke fixture, and MUST propose a crate name. The dialog SHALL NOT emit any Rust source or write any files outside `crates/<name>-strategy/` until the user accepts the proposed intent. Every accepted clarification MUST be persisted to the DecisionRecord at `crates/<name>-strategy/.author/decisions.jsonl` (see *Structured DecisionRecord is the authoritative dialog state*) before the next LLM turn is dispatched, so that the dialog is resumable across chat-history compaction.

#### Scenario: NL seed supplied

- **WHEN** the operator runs `strategy-gpt author "trend-follow SPY with ATR stops, daily bars"`
- **THEN** the dialog opens with the seed as initial context, writes a `dialog_started` event to the DecisionRecord, asks clarifying questions (e.g. universe scope, holding-period range, stop construction details, smoke window), and produces an `AuthorIntent` once the operator accepts it

#### Scenario: No seed supplied

- **WHEN** the operator runs `strategy-gpt author` with no positional argument
- **THEN** the dialog opens cold, writes a `dialog_started` event with `seed=null`, and the first LLM turn asks the operator what they want to author before any other clarification

#### Scenario: Decision panel renders between turns

- **WHEN** an operator clarification has been accepted and the next LLM turn is about to be dispatched
- **THEN** the locked-in decisions panel is rendered (unless `--quiet`), showing the current value of every locked field

### Requirement: Emit / build / smoke repair loop

The Author SHALL drive emission, build, and smoke through a repair loop with configurable per-stage budgets (default `k_repair=2`). The loop MUST: write the LLM-emitted files to `crates/<name>-strategy/` on every attempt; run `BuildPipeline.lint()` and package-scoped `cargo build -p <name>-strategy`; on successful build, run a smoke backtest using the fixture declared in `smoke.toml`. Build failures, lint rejections, smoke panics, and smoke sanity-trip cascades MUST each produce a feedback string the next LLM attempt receives. The loop MUST emit structured progress events to the `event_sink` for every substep so the CLI can surface in-flight feedback (see *Structured operation-feedback event stream during emit/build/smoke*).

#### Scenario: Loop runs the canonical substep sequence per attempt

- **WHEN** an emit/build/smoke attempt is dispatched
- **THEN** the Author writes the emitted files under `crates/<name>-strategy/`, runs `BuildPipeline.lint()`, runs package-scoped `cargo build -p <name>-strategy`, and on successful build runs the smoke backtest against the fixture declared in `smoke.toml`, in that order

#### Scenario: Smoke panics, repair loop runs

- **WHEN** the strategy compiles but panics on the first bar of the smoke fixture
- **THEN** the panic message is surfaced into the next LLM attempt's feedback section, the previous emission is included verbatim above it, and the repair counter increments

#### Scenario: Progress events fire for each substep

- **WHEN** an attempt of the emit/build/smoke loop runs
- **THEN** the event sink receives, in order, `repair_attempt_started`, `file_written` per emitted file, `lint_started`, `lint_completed`, `cargo_build_started`, `cargo_build_completed`, `smoke_fetch_started`, `smoke_fetch_completed`, `smoke_run_started`, `smoke_run_completed`, and finally `repair_attempt_completed`

#### Scenario: Repair budget exhausted hands control back to the operator

- **WHEN** the emit-build-smoke stage exhausts its `k_repair` budget without a passing attempt
- **THEN** a `repair_budget_exhausted` event is appended to the DecisionRecord, the dialog regains control with the failure trail summarized as a first-class turn, and the operator is offered a menu: (1) suggest an alternative approach in natural language, (2) retry with an extended budget, (3) edit a specific decision (e.g. `param_sketch`, `mechanism_summary`), or (4) abort

#### Scenario: Operator suggests alternative approach after exhaustion

- **WHEN** the operator picks option 1 from the repair-exhausted menu and types a natural-language alternative (e.g. "use a Bollinger-band breakout instead of ATR stops")
- **THEN** the LLM amends the intent by writing `decision_amended` events for the affected fields, the locked-in panel re-renders, and the emit/build/smoke loop restarts with fresh budget against the amended intent

#### Scenario: Operator extends budget and retries after exhaustion

- **WHEN** the operator picks option 2 from the repair-exhausted menu and supplies new budget values (e.g. `k_repair_emit=4`)
- **THEN** the emit/build/smoke loop restarts with the existing intent and the new budgets; the prior attempt history is included in the LLM's repair feedback

#### Scenario: Operator aborts after exhaustion

- **WHEN** the operator picks option 4 from the repair-exhausted menu
- **THEN** the command exits non-zero, the crate files (including any partial `src/lib.rs`, `Cargo.toml`, and `.author/decisions.jsonl`) remain on disk for inspection, and no further LLM call is made
