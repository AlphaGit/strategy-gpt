## 1. Workspace refactor

- [x] 1.1 Edit `crates/Cargo.toml`: replace explicit `members = [...]` with `members = ["*"]`. Keep `[workspace.package]`, `[workspace.dependencies]`, `[profile.*]` intact.
- [x] 1.2 Run `cargo check --workspace` from `crates/` and confirm output matches the pre-refactor run (same crates built, same warnings).
- [x] 1.3 Add a brief note to `CLAUDE.md` under the repo layout section explaining that `crates/Cargo.toml` uses a glob so author-emitted crates are auto-included.

## 2. Author intent + dialog driver

- [x] 2.1 Add `python/strategy_gpt/author.py` with `AuthorIntent`, `AuthorDeps`, `AuthoredStrategy`, `SmokeSpec` dataclasses per the design's library seam.
- [x] 2.2 Implement `run_intent_dialog(seed: str | None, *, reasoning_client, crates_dir: Path) -> AuthorIntent`: orchestrates clarifying questions, proposes a name, auto-detects edit-mode by checking `crates/<name>-strategy/` existence, loads existing artifacts on edit, returns a frozen `AuthorIntent`.
- [x] 2.3 Add a stage-prompts module (e.g. `python/strategy_gpt/prompts_author.py`) with: a dialog system prompt that includes the `Strategy` trait surface dump, the build-pipeline whitelist, and the always-on few-shot exemplars (`vxx-strategy` + `example-strategy`); and an emit-stage prompt scaffold that accepts an `AuthorIntent` and emits `src/lib.rs` + `Cargo.toml` + `smoke.toml` (+ optional `experiment.yaml`) as a markdown payload.
- [x] 2.4 Add unit tests for `run_intent_dialog` with a stubbed reasoning client driving a scripted conversation (covers: new-strategy happy path, edit-mode auto-detection on name collision, dialog returning a `--verify=batch` intent with `ExperimentSpec` populated).

## 3. Emit + build + smoke loop

- [x] 3.1 Implement `author_strategy(intent, *, deps) -> AuthoredStrategy`:
  - Write the proposed files to `crates/<intent.name>-strategy/` on every attempt (overwriting prior attempts in place).
  - Invoke `BuildPipeline.lint()` then `BuildPipeline.build()` package-scoped on the new crate. Non-whitelisted crates are a hard reject — surface the diagnostic into the next repair feedback.
  - On successful build, run a smoke backtest using `data-gateway` to fetch bars per `smoke_spec` and the existing engine smoke entry point. Smoke pass criterion mirrors the tester's: no panic, no repeated sanity trips, at least one simulated trade emitted (configurable as a smoke knob if needed).
  - Drive the emit and build/smoke stages through `repair.run_stage_with_repair` with separate `RepairConfig` instances; feedback synthesizers translate cargo / smoke diagnostics into the LLM prompt.
- [x] 3.2 On budget exhaustion, return control to `run_intent_dialog` so the user can adjust intent (e.g. expand smoke window, swap mechanism). The dialog then re-invokes `author_strategy` with the revised intent.
- [x] 3.3 On success, persist `intent.toml` and (if `--verify=batch`) `experiment.yaml` alongside `src/lib.rs` / `Cargo.toml` / `smoke.toml`. Return `AuthoredStrategy(name, crate_path, artifact_hash, intent)`.
- [x] 3.4 Unit tests for `author_strategy` with: stubbed reasoning client returning a known-good `example-strategy`-shaped emission (build + smoke pass on first try); a stubbed client that emits a non-whitelisted crate (build rejects, repair fixes it, build passes); a stubbed client that never converges within budget (dialog regains control).

## 4. CLI wiring

- [x] 4.1 Add `author` command to `python/strategy_gpt/cli.py` with options: positional `[idea]`, `--verify=batch`, `--k-repair-emit=N`, `--k-repair-build=N`, `--model=<name>` (optional override for reasoning client).
- [x] 4.2 The CLI constructs `AuthorDeps` (real `BuildPipeline`, real `DataGateway`, configured `ReasoningClient`), runs `run_intent_dialog` interactively, then calls `author_strategy`. On success, prints the crate path and a next-step hint (`strategy-gpt run <name>` or `strategy-gpt hypothesize <name>`).
- [x] 4.3 Add CLI-level tests using Typer's `CliRunner` with a fixture reasoning client and a temporary `crates/` to verify exit codes, output formatting, and the next-step hint.

## 5. Smoke + experiment specs

- [x] 5.1 Define the `smoke.toml` schema in `python/strategy_gpt/author.py` (pydantic model) and document it in the `author` capability spec.
- [x] 5.2 Define the `experiment.yaml` schema for author's `--verify=batch` mode by reusing the existing `experiment_spec` module if possible, otherwise documenting the subset author emits.
- [x] 5.3 Define the `intent.toml` schema (name, description, mechanism summary, param schema sketch, smoke spec, optional experiment spec, baseline crate path if edit-mode). Round-trip serializable.

## 6. Documentation

- [x] 6.1 Add a how-to page `docs/how-to/author-a-strategy.md` walking through: invoking `author` with and without a positional seed, what the dialog looks like, how edit-mode is triggered, what files land on disk, and how to follow up with `strategy-gpt run` and `strategy-gpt hypothesize`.
- [x] 6.2 Update `docs/tutorials/authoring-a-strategy.md` if it currently documents hand-authored strategies — either redirect it to the new how-to or repurpose it to demonstrate the `author` command end-to-end with a frozen NL prompt and a stubbed reasoning client for reproducibility.
- [x] 6.3 Update `CLAUDE.md` "Module roles" to add an "Author" entry, and the `Repo layout` section to note `python/strategy_gpt/author.py`.

## 7. Spec sync and verification

- [x] 7.1 Run `openspec validate add-author-command --strict` and resolve any spec-format issues.
- [x] 7.2 Run `make lint` and `make test`. The new tests under `python/tests/test_author.py` MUST be green; the workspace `cargo check --workspace` MUST be unchanged after the glob refactor.
