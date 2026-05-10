## ADDED Requirements

### Requirement: Rust formatting and lint use tool defaults

The repository SHALL enforce Rust style using `rustfmt` with default rules and `clippy` with default lints. There MUST NOT be a custom `rustfmt.toml` or `clippy.toml` introducing project-specific overrides. The canonical commands are:

- `cargo fmt --all -- --check`
- `cargo clippy --workspace --all-targets -- -D warnings`

#### Scenario: Committing unformatted Rust

- **WHEN** a Rust file is committed with formatting that `rustfmt` would change
- **THEN** the pre-commit hook fails with the diff and rejects the commit

#### Scenario: Clippy default warning surfaces

- **WHEN** Rust code triggers any default clippy lint at warning or higher
- **THEN** `cargo clippy --workspace --all-targets -- -D warnings` exits non-zero and the pre-commit hook rejects the commit

### Requirement: Python lint uses ruff with strict rule selection

The repository SHALL enforce Python lint with `ruff check`. The active rule selection MUST include at least: `E`, `F`, `W`, `I`, `B`, `UP`, `SIM`, `RUF`, `S`, `N`, `PT`, `ANN`, `C4`, `ERA`, and a `PL` subset. Per-rule ignores MUST be justified by an in-line comment or in the central `[tool.ruff.lint]` config with a brief reason.

#### Scenario: Unannotated public function

- **WHEN** a public function in `python/strategy_gpt/` is declared without type annotations
- **THEN** `ruff check` reports an `ANN` rule violation and the pre-commit hook rejects the commit

#### Scenario: Bandit security smell

- **WHEN** Python code calls `eval`, `exec`, or `subprocess.Popen(shell=True)` without justification
- **THEN** `ruff check` reports the corresponding `S` rule violation

#### Scenario: Documented ignore is accepted

- **WHEN** a specific rule is suppressed via `# noqa: <rule>` with a comment explaining why
- **THEN** `ruff check` accepts the suppression and does not flag the line

### Requirement: Python format uses ruff format

The repository SHALL enforce Python formatting with `ruff format`. The canonical command is `ruff format --check python/`.

#### Scenario: Unformatted Python

- **WHEN** a Python file is committed with formatting that `ruff format` would change
- **THEN** the pre-commit hook fails with the diff and rejects the commit

### Requirement: Python type checking uses mypy strict

The repository SHALL run `mypy --strict` over `python/strategy_gpt/`. Strict mode includes all checks `mypy --strict` enables, including `disallow-untyped-defs`, `warn-return-any`, and `no-implicit-reexport`.

#### Scenario: Untyped def in package code

- **WHEN** a function in `python/strategy_gpt/` lacks parameter or return type annotations
- **THEN** `mypy --strict` reports the missing annotation and the pre-commit hook rejects the commit

#### Scenario: Implicit Any inferred from external library

- **WHEN** a typed call site receives an implicitly `Any`-typed value from a third-party library lacking stubs
- **THEN** `mypy --strict` warns; the contributor MUST add a stub or an explicit `Any` annotation with a comment

### Requirement: Pre-commit framework drives all gates

The repository SHALL contain a `.pre-commit-config.yaml` at the root that wires every gate (rustfmt, clippy, ruff check, ruff format, mypy) plus baseline file hygiene hooks (`trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-toml`, `check-added-large-files`). Hook versions MUST be pinned.

#### Scenario: First-time setup

- **WHEN** a contributor runs `pre-commit install`
- **THEN** subsequent `git commit` invocations run the full hook suite on staged files

#### Scenario: Hook version drift

- **WHEN** a contributor updates a hook to a version other than the pinned one
- **THEN** the configuration change is visible in the diff and reviewers can accept or reject explicitly

### Requirement: Rust hooks shell out to the project toolchain

The pre-commit configuration SHALL invoke `cargo fmt` and `cargo clippy` via `system`-language hooks that use the contributor's `rust-toolchain.toml`-pinned toolchain. The configuration MUST NOT introduce a parallel Rust toolchain via pre-commit's installer mechanism.

#### Scenario: Toolchain pin is the source of truth

- **WHEN** `rust-toolchain.toml` pins Rust to a specific version
- **THEN** the pre-commit hooks use that version, not a pre-commit-managed alternative

### Requirement: Unified entry point — `make lint`

The repository SHALL provide a `Makefile` at the root with at minimum the targets `lint`, `fmt`, `lint-rust`, `lint-python`, and `test`. `make lint` MUST run the same set of checks as `pre-commit run --all-files`.

#### Scenario: Local full-tree run

- **WHEN** a contributor runs `make lint`
- **THEN** rustfmt --check, clippy, ruff check, ruff format --check, and mypy run over the entire tree and exit non-zero on any failure

#### Scenario: CI invocation

- **WHEN** CI calls `make lint`
- **THEN** the same suite runs and produces an outcome equivalent to `pre-commit run --all-files`

### Requirement: Type-checker scope

`mypy --strict` SHALL run on `python/strategy_gpt/`. Tests (when present under `python/tests/`) MAY run mypy without strict. Ingestion scripts under `kb/` SHALL be excluded from mypy until they stabilize, and the exclusion MUST be declared explicitly in `[tool.mypy.overrides]`.

#### Scenario: Ingestion script outside scope

- **WHEN** `kb/ingest.py` contains untyped functions
- **THEN** `mypy --strict` does not report violations because the path is excluded

### Requirement: Repository state is green at all times

After this change merges, `make lint` SHALL exit zero on the main branch. CI MUST refuse to merge any pull request whose `make lint` fails.

#### Scenario: Initial green baseline

- **WHEN** this change merges to main
- **THEN** `make lint` run from a clean checkout exits zero
