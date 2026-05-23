# Spec: author

## Purpose

Drives interactive LLM-led creation (and editing) of strategy crates from human intent. Elicits a structured `AuthorIntent` through a clarifying dialog, then runs an emit / build / smoke repair loop that produces a working `crates/<name>-strategy/` on disk — `src/lib.rs`, `Cargo.toml`, `intent.toml`, `smoke.toml`, and optionally `experiment.yaml`. Success is defined as a clean build plus a smoke backtest that produces at least one trade; no metrics evaluation, no baseline comparison, no ledger row. Exposes a library seam (`author_strategy`) separate from the CLI so future callers (e.g. the hypothesis loop's `generate` stage) can reuse the same emit/build/smoke pipeline with a programmatically constructed intent.

## Requirements

### Requirement: Interactive intent dialog

The Author SHALL accept an optional natural-language seed and drive an interactive LLM dialog that produces a structured `AuthorIntent` before any code is emitted. The dialog MUST ask clarifying questions about universe, mechanism, parameter sketch, and smoke fixture, and MUST propose a crate name. The dialog SHALL NOT emit any Rust source or write any files outside `crates/<name>-strategy/` until the user accepts the proposed intent.

#### Scenario: NL seed supplied

- **WHEN** the operator runs `strategy-gpt author "trend-follow SPY with ATR stops, daily bars"`
- **THEN** the dialog opens with the seed as initial context, asks clarifying questions (e.g. universe scope, holding-period range, stop construction details, smoke window), and produces an `AuthorIntent` once the operator accepts it

#### Scenario: No seed supplied

- **WHEN** the operator runs `strategy-gpt author` with no positional argument
- **THEN** the dialog opens cold and the first LLM turn asks the operator what they want to author before any other clarification

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

### Requirement: Edit-mode auto-detection

When the dialog proposes a crate name that collides with an existing `crates/<name>-strategy/`, the Author SHALL inform the operator and ask whether to edit the existing crate or pick a different name. If the operator chooses to edit, the Author MUST load the existing `intent.toml`, `src/lib.rs`, `Cargo.toml`, and `smoke.toml` into the LLM context and frame subsequent emissions as modifications.

#### Scenario: Name collision triggers edit prompt

- **WHEN** the dialog proposes `spy-atr` and `crates/spy-atr-strategy/` already exists
- **THEN** the LLM informs the operator of the collision and asks "edit `spy-atr` or pick a different name?", and on edit loads the four existing artifact files into the next prompt turn

#### Scenario: Operator picks a different name

- **WHEN** the dialog proposes `spy-atr`, the crate already exists, and the operator asks for a different name
- **THEN** the dialog continues without loading the existing crate's contents and re-proposes a non-colliding name

### Requirement: Crate artifact set

A successful author run SHALL produce the following files in `crates/<name>-strategy/`, all valid and round-trip serializable:

- `src/lib.rs` — strategy source implementing the sealed `Strategy` trait
- `Cargo.toml` — manifest declaring only deps within the build-pipeline whitelist
- `intent.toml` — structured intent record (name, description, mechanism summary, param schema sketch, smoke spec, optional experiment spec, optional baseline crate path)
- `smoke.toml` — fixture data spec (symbols, resolution, range, provider)

When the run was invoked with `--verify=batch`, the directory SHALL additionally contain `experiment.yaml` describing the full-batch verification spec.

#### Scenario: Successful run writes the four required files

- **WHEN** an author session for `spy-atr` succeeds without `--verify=batch`
- **THEN** `crates/spy-atr-strategy/{src/lib.rs,Cargo.toml,intent.toml,smoke.toml}` all exist, the manifest declares only whitelisted crates, and `intent.toml` deserializes back into the same `AuthorIntent` that produced the run

#### Scenario: --verify=batch writes the experiment.yaml

- **WHEN** an author session is invoked with `--verify=batch` and the full-batch check passes
- **THEN** `crates/<name>-strategy/experiment.yaml` exists alongside the other four files

### Requirement: Hard reject of non-whitelisted crates

The Author SHALL reject any LLM emission whose `Cargo.toml` declares a dependency outside the build-pipeline allowed-crate whitelist. The rejection MUST surface the offending crate name and the whitelist rule into the next repair-loop feedback string. The Author MUST NOT request, install, or propose additions to the whitelist.

#### Scenario: Manifest declares a non-whitelisted crate

- **WHEN** the LLM emits a `Cargo.toml` declaring `reqwest = "0.11"` and `reqwest` is not on the whitelist
- **THEN** the build pipeline rejects the manifest, the repair feedback names `reqwest` and the whitelist rule, and the next LLM attempt is expected to drop or substitute the dependency

#### Scenario: Repair loop cannot satisfy whitelist within budget

- **WHEN** the LLM persistently emits a non-whitelisted dep across the configured repair budget
- **THEN** the dialog regains control, summarizes the failed attempts, and asks the operator how to proceed (e.g. revise mechanism, broaden allowed crates out-of-band, give up)

### Requirement: Emit / build / smoke repair loop

The Author SHALL drive emission, build, and smoke through a repair loop with configurable per-stage budgets (default `k_repair=2`). The loop MUST: write the LLM-emitted files to `crates/<name>-strategy/` on every attempt; run `BuildPipeline.lint()` and package-scoped `cargo build -p <name>-strategy`; on successful build, run a smoke backtest using the fixture declared in `smoke.toml`. Build failures, lint rejections, smoke panics, and smoke sanity-trip cascades MUST each produce a feedback string the next LLM attempt receives. The Author MUST emit structured progress events to the `event_sink` for every substep so the CLI can surface in-flight feedback (see *Structured operation-feedback event stream during emit/build/smoke*).

#### Scenario: Loop runs the canonical substep sequence per attempt

- **WHEN** an emit/build/smoke attempt is dispatched
- **THEN** the Author writes the emitted files under `crates/<name>-strategy/`, runs `BuildPipeline.lint()`, runs package-scoped `cargo build -p <name>-strategy`, and on successful build runs the smoke backtest against the fixture declared in `smoke.toml`, in that order

#### Scenario: Repair-loop default budgets

- **WHEN** the Author is invoked without explicit `--k-repair-emit` / `--k-repair-build` flags
- **THEN** each stage runs with `k_repair=2`, yielding three total attempts per stage (one initial plus two repairs)

### Requirement: Repair prompt carries diagnostic AND previous emission

Every repair attempt's prompt SHALL contain BOTH the validator's failure diagnostic (rustc stderr, lint rejection summary, whitelist offender, smoke panic message, or zero-trade signal) AND the verbatim text of the LLM's previous failed emission, rendered under a "Your previous attempt (revise this; do not start from scratch)" section. The prompt MUST instruct the LLM to preserve unaffected parts of the previous emission and target the change to what the diagnostic identifies. The `emit_files` API surface is single-turn (no conversation continuity), so the previous emission must be re-supplied in-prompt; relying on chat history is not acceptable.

#### Scenario: Build fails, repair prompt includes diagnostic and previous emission

- **WHEN** the first emission fails `cargo build` with a borrow-checker error and the repair loop dispatches a second attempt
- **THEN** the second emit-stage user prompt contains the rustc diagnostic under a "Why the previous attempt was rejected" section AND the full text of the first emission under "Your previous attempt (revise this; do not start from scratch)"

#### Scenario: Smoke panics, repair loop runs

- **WHEN** the strategy compiles but panics on the first bar of the smoke fixture
- **THEN** the panic message is surfaced into the next LLM attempt's feedback section, the previous emission is included verbatim above it, and the repair counter increments

#### Scenario: Non-whitelisted dep surfaces in repair prompt

- **WHEN** the first emission declares a non-whitelisted crate and the build pipeline rejects it
- **THEN** the next attempt's prompt names the offending crate and the whitelist rule in the feedback section, the previous emission is rendered verbatim above it, and the LLM is expected to drop or substitute the dependency rather than re-derive the whole crate

#### Scenario: Repair budget exhausted

- **WHEN** the emit-build-smoke stage exhausts its `k_repair` budget without a passing attempt
- **THEN** control returns to the interactive dialog, the LLM summarizes the failed attempts, and the operator can adjust the intent (e.g. expand smoke window, swap mechanism) before retrying

### Requirement: Cargo build progress ticks during long builds

While `cargo build` is in flight, the Author SHALL emit `CargoBuildProgress` events to the event sink at regular intervals (default: every 2 seconds) so the CLI can surface in-flight progress to the operator. Each tick carries the elapsed seconds since the build started. The watcher MUST stop the moment the build returns (success or failure), so a fast or stubbed build emits no ticks. Tick emission MUST run concurrently with the build (e.g. on a daemon thread); it MUST NOT block on the build.

#### Scenario: Long build emits intermediate progress events

- **WHEN** the build pipeline blocks for several seconds before returning (typical for a real `cargo build`)
- **THEN** the event sink receives one or more `CargoBuildProgress` events between `CargoBuildStarted` and `CargoBuildCompleted`, each carrying a strictly-increasing `elapsed_seconds`

#### Scenario: Instant build emits no progress ticks

- **WHEN** the build pipeline returns immediately (e.g. test stub, cache hit)
- **THEN** the event sink receives `CargoBuildStarted` followed by `CargoBuildCompleted` with no intervening `CargoBuildProgress` event

### Requirement: Smoke passes is the success bar

Author success SHALL be defined as a successful build followed by a smoke backtest that does not panic, does not trip the engine's sanity bounds repeatedly, and produces at least one simulated trade. The Author MUST NOT evaluate metrics, MUST NOT compare against any baseline, and MUST NOT record any verdict in any ledger.

#### Scenario: Smoke passes, command exits success

- **WHEN** the emitted strategy compiles and smoke runs without panic, sanity-trip cascade, or zero-trade output
- **THEN** the command exits zero, the crate path is printed, and no ledger row is written

#### Scenario: Smoke runs but emits zero trades

- **WHEN** the emitted strategy compiles and runs without error but produces no simulated trades on the smoke fixture
- **THEN** the smoke step fails and the repair loop receives feedback identifying the zero-trade outcome

### Requirement: Optional full-batch verification

When invoked with `--verify=batch`, the Author SHALL, after a successful smoke pass, run a full walk-forward batch using the engine against the `experiment.yaml` produced during the dialog. A failed batch MUST be surfaced into the dialog the same way a failed smoke is (LLM summarizes, operator decides next step); it MUST NOT silently overwrite the on-disk crate.

#### Scenario: Full batch passes

- **WHEN** `--verify=batch` is set, smoke passes, and the engine reports the batch completed without panic across all configured folds
- **THEN** the command exits success and `experiment.yaml` is persisted alongside the other crate files

#### Scenario: Full batch fails after smoke succeeded

- **WHEN** `--verify=batch` is set, smoke passes, and the full batch panics on a fold beyond the smoke window
- **THEN** the dialog resumes with the failed-fold diagnostics, the operator decides how to proceed, and the crate files remain on disk for inspection

### Requirement: Few-shot exemplars are always loaded

Every author LLM prompt SHALL include the source of `crates/vxx-strategy/` and `crates/example-strategy/` as few-shot exemplars covering the sealed `Strategy` trait surface, `ParamSchema` declaration, `Context` capability use, and `Cargo.toml` manifest shape. The Author MUST NOT prompt without exemplars even when token cost would be reduced by omitting them.

#### Scenario: Exemplars present in every prompt

- **WHEN** any author LLM call is dispatched (dialog turn, emit attempt, repair attempt)
- **THEN** the prompt contains the full text of `crates/vxx-strategy/src/lib.rs`, `crates/vxx-strategy/Cargo.toml`, `crates/example-strategy/src/lib.rs`, and `crates/example-strategy/Cargo.toml`

### Requirement: Library seam separate from CLI

The Author SHALL expose a library function `author_strategy(intent: AuthorIntent, *, deps: AuthorDeps) -> AuthoredStrategy` independent of the CLI. The library function MUST accept a fully-formed `AuthorIntent` (i.e., the dialog stage is not required when calling the library directly) so future callers (e.g. the hypothesis loop's `generate` stage) can supply a programmatically-derived intent and reuse the same emit / build / smoke loop.

#### Scenario: Library call bypasses dialog

- **WHEN** a Python caller constructs an `AuthorIntent` directly and calls `author_strategy(intent, deps=deps)`
- **THEN** no dialog runs, the emit / build / smoke loop runs end-to-end against the supplied intent, and the call returns an `AuthoredStrategy` on success or raises on terminal failure

#### Scenario: CLI is a thin wrapper

- **WHEN** the `strategy-gpt author` CLI is invoked
- **THEN** the CLI constructs `AuthorDeps`, runs `run_intent_dialog` to produce an `AuthorIntent`, then delegates to `author_strategy`
