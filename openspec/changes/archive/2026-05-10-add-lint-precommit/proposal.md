## Why

The polyglot rewrite has no enforced style or lint baseline. Rust code currently passes `cargo check` and `cargo clippy` with defaults; Python code has no linter wired into the workflow. Without a consistent gate, drift accumulates: inconsistent formatting, unused imports, type errors, dead code, security smells. We need a single command that runs everything before a commit and the same checks on CI, so style is never a code-review topic.

## What Changes

- Add `rustfmt` + `clippy` as required Rust gates using **default** rules (no custom rule lists). `cargo fmt --check` and `cargo clippy --all-targets -- -D warnings` are the canonical Rust commands.
- Add `ruff` (lint + format) and `mypy` as required Python gates with **strict** configuration: extensive rule selection (`E`, `F`, `I`, `B`, `UP`, `SIM`, `RUF`, `S`, `N`, `PT`, `ANN`, `C4`, `ERA`, `PL`), `ruff format --check` enforced, and `mypy --strict` over `python/strategy_gpt/`.
- Add a `pre-commit` configuration that runs all of the above on staged files and refuses commits that fail any gate.
- Add a `make lint` (or equivalent) entry point that runs the full suite locally without the `pre-commit` framework, useful in CI and ad-hoc.
- Document the workflow in `CLAUDE.md` and `python/README.md`.
- Provide a one-command developer setup (`pre-commit install`) and document it.

## Capabilities

### New Capabilities

- `lint-and-precommit`: defines the formatter, linter, and type-checker configuration for both languages, the pre-commit hook surface, and the unified command entry point.

### Modified Capabilities

None — new capability.

## Impact

- **Code**: New configuration files: `.pre-commit-config.yaml` (root), updated `python/pyproject.toml` (`[tool.ruff]`, `[tool.ruff.lint]`, `[tool.ruff.format]`, `[tool.mypy]`), optional `.rustfmt.toml` only if defaults need any tweak (target: none). New `Makefile` (or `justfile`) at root with `lint`, `fmt`, `test` targets.
- **Dependencies (Python)**: `ruff>=0.7`, `mypy>=1.13`, `pre-commit>=4` added to the dev group.
- **Developer workflow**: One-time `pre-commit install`. Subsequent commits trigger the suite automatically.
- **CI**: Future CI (task 13.3 of `rewrite-architecture`) will run `pre-commit run --all-files` plus `cargo test` and `pytest`.
- **Out of scope**: Auto-formatting on save (editor-side), commit-message linting, license-header enforcement, secrets scanning, dependency-vulnerability scanning. These can land as separate changes.
