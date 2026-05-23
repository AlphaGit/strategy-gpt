## 1. DecisionRecord foundation

- [x] 1.1 Define `DecisionEvent` typed-union dataclasses (`DialogStarted`, `DecisionLocked`, `DecisionAmended`, `IntentFinalized`, `RepairBudgetExhausted`) with a `schema_version` field in `python/strategy_gpt/author_decisions.py`
- [x] 1.2 Add `field` enum / Literal type covering `crate_name`, `universe`, `mechanism_summary`, `param_sketch`, `smoke_spec`, `experiment_spec`, `edit_mode_target`
- [x] 1.3 Implement `DecisionRecord` class with `append(event)` (JSONL writer, fsync per line), `load(path)` (yields events in order), and `project()` (returns `{field: current_value}` last-write-wins)
- [x] 1.4 Add round-trip serialization tests (`python/tests/test_author_decisions.py`): emit each event type, reload, assert equality
- [x] 1.5 Add projection test: emit `locked(name=spy-atr)`, `locked(universe=SPY)`, `amended(universe=SPY,QQQ)`, assert `project()["universe"] == "SPY,QQQ"` and `project()["crate_name"] == "spy-atr"`
- [x] 1.6 Add `crates/*-strategy/.author/` to `.gitignore`

## 2. Wire DecisionRecord into the dialog

- [x] 2.1 Add `decision_record_path` to `AuthorDeps` (default: resolved from intent's crate name once known)
- [x] 2.2 In `run_intent_dialog` (`python/strategy_gpt/author.py`), open the DecisionRecord at the first turn that has a crate name proposal accepted; emit `dialog_started` event carrying the optional seed and the model name
- [x] 2.3 Replace the in-memory accumulation of accepted clarifications with `DecisionRecord.append(DecisionLocked(...))`; rewrite intent assembly to call `DecisionRecord.project()`
- [x] 2.4 Update the LLM system prompt assembly in `python/strategy_gpt/prompts_author.py` to inject "Decisions so far:" block built from `DecisionRecord.project()` on every turn (replaces reliance on chat history)
- [x] 2.5 Detect operator amendments to previously-locked fields and emit `DecisionAmended` events
- [x] 2.6 Emit `IntentFinalized` event carrying the serialized `AuthorIntent` immediately before handing control to `author_strategy`
- [x] 2.7 Add test (`test_author_dialog_compaction.py`): drive a dialog with three accepted clarifications via a stub LLM, truncate the LLM chat history, run a fourth turn, assert no previously-settled clarification is re-asked

## 3. Locked-in decisions panel

- [ ] 3.1 Implement `render_decisions_panel(record_projection)` in `python/strategy_gpt/cli.py` (or a new `author_ui.py` if `cli.py` becomes unwieldy): fixed-width labels, head-and-ellipsis for long fields, ≤15 lines, visual separators
- [ ] 3.2 Invoke `render_decisions_panel` from `run_intent_dialog` between turns (after a decision is locked, before the next LLM dispatch)
- [ ] 3.3 Gate the panel behind a `quiet` flag passed through `AuthorDeps`; suppress the panel when set
- [ ] 3.4 Add `--quiet` flag to `strategy-gpt author` and plumb to `AuthorDeps.quiet`
- [ ] 3.5 Add snapshot/golden tests for panel rendering: empty record, mid-dialog (3 fields), post-amendment, long-mechanism truncation

## 4. Operation-feedback event stream

- [ ] 4.1 Define `AuthorEvent` typed-union dataclasses (`FileWritten`, `LintStarted`, `LintCompleted`, `CargoBuildStarted`, `CargoBuildCompleted`, `SmokeFetchStarted`, `SmokeFetchCompleted`, `SmokeRunStarted`, `SmokeRunCompleted`, `RepairAttemptStarted`, `RepairAttemptCompleted`) in `python/strategy_gpt/author_events.py`
- [ ] 4.2 Add `event_sink: Callable[[AuthorEvent], None]` to `AuthorDeps` with a no-op default; document the type alias `AuthorEventSink`
- [ ] 4.3 Thread `event_sink` through `author_strategy`, the emit/build/smoke loop helpers, the cargo subprocess wrapper, and the smoke fetch/run paths; emit the corresponding event at every substep boundary
- [ ] 4.4 Implement `cli_event_renderer(verbose: bool, quiet: bool)` returning a sink that prints human-readable progress lines; collapse to one-line spinner under `quiet`; include per-line cargo/rustc stream under `verbose`
- [ ] 4.5 Wire `cli_event_renderer` into the CLI `author` command; add `--verbose` flag
- [ ] 4.6 Add test (`test_author_events.py`): run a successful smoke loop with a collecting sink, assert the event sequence matches the spec scenario exactly
- [ ] 4.7 Add test: default sink (no override) does not print to stdout or stderr (capture via `capsys`)

## 5. Repair-budget exhaustion control transfer

- [ ] 5.1 Introduce a `RepairExhausted` result type returned by the internal `_run_emit_build_smoke_loop` function carrying `attempts: list[AttemptTrail]`, `last_intent: AuthorIntent`, `budgets_used: dict`
- [ ] 5.2 Keep `author_strategy` library seam signature-compatible: on exhaustion *from a library caller* (no dialog wrapper), raise `RepairBudgetExhaustedError` as today; from the dialog wrapper, return the result up to the dialog
- [ ] 5.3 In `run_intent_dialog`, catch the `RepairExhausted` result and dispatch a new dialog turn whose system prompt includes the failure trail summary; emit a `RepairBudgetExhausted` event to the DecisionRecord
- [ ] 5.4 Implement the operator menu (1: suggest alternative, 2: retry with extended budget, 3: edit a specific decision, 4: abort); use `AskUserQuestion`-equivalent for the CLI (numbered prompt) — no new external deps
- [ ] 5.5 Option 1: dispatch a "amend the intent based on this NL suggestion" LLM turn that produces `DecisionAmended` events, then restart the loop with fresh budget
- [ ] 5.6 Option 2: prompt for new `k_repair_emit` / `k_repair_build` values, validate they are positive ints, restart the loop with the existing intent and the new budgets (prior attempt history flows into the next LLM repair feedback)
- [ ] 5.7 Option 3: prompt for a field name; dispatch an LLM turn scoped to revising just that field; restart the loop
- [ ] 5.8 Option 4: exit non-zero; leave the crate files and `.author/decisions.jsonl` on disk
- [ ] 5.9 Add test (`test_author_repair_exhaustion.py`): stub the build to always fail, drive the dialog to exhaustion, simulate operator picking each of the four options, assert the right next step in each case

## 6. Documentation and ledger entries

- [ ] 6.1 Update `docs/how-to/cli-cookbook.md` Author section to document `--quiet` and `--verbose`, the locked-in panel, and the repair-exhausted menu
- [ ] 6.2 Update `docs/how-to/author-a-strategy.md` with a "When the repair loop fails" section walking the four menu options
- [ ] 6.3 Update `docs/tutorials/author-a-strategy.md` to call out the locked-in decisions panel ("you should see the panel update after this turn") so readers know to look for it
- [ ] 6.4 Update `CLAUDE.md` Author module-role bullet to mention the DecisionRecord as the authoritative dialog state

## 7. Quality gates

- [ ] 7.1 `make lint` is clean (ruff, ruff format, mypy --strict on `python/strategy_gpt/`)
- [ ] 7.2 `make test` is clean; the new tests under `python/tests/` are included
- [ ] 7.3 Manual smoke: run `strategy-gpt author "trend-follow SPY"` end-to-end against a real LLM; verify the panel renders, progress events surface, and the on-disk crate is byte-identical to a baseline run made with `--quiet` (i.e. the new UX is additive, not perturbing)
- [ ] 7.4 Manual smoke: force a repair-exhaustion by setting `--k-repair-emit=0`; verify each of the four menu options behaves per spec
