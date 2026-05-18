## MODIFIED Requirements

### Requirement: Unified entry point — `make lint`

The repository SHALL provide a `Makefile` at the root with at minimum the targets `lint`, `fmt`, `lint-rust`, `lint-python`, `docs-build`, `docs-serve`, and `test`. `make lint` MUST run the same set of checks as `pre-commit run --all-files`, and MUST additionally invoke `mkdocs build --strict` so that broken documentation cross-references fail the lint gate.

#### Scenario: Local full-tree run

- **WHEN** a contributor runs `make lint`
- **THEN** rustfmt --check, clippy, ruff check, ruff format --check, mypy, and `mkdocs build --strict` run over the entire tree and exit non-zero on any failure

#### Scenario: CI invocation

- **WHEN** CI calls `make lint`
- **THEN** the same suite runs and produces an outcome equivalent to `pre-commit run --all-files` plus a `mkdocs build --strict` pass

#### Scenario: Broken docs link fails lint

- **WHEN** a `.md` file under `docs/` contains a cross-reference to a non-existent file or anchor and a contributor runs `make lint`
- **THEN** lint exits non-zero with the mkdocs error identifying the offending link

## ADDED Requirements

### Requirement: Pre-commit hook for docs build

The `.pre-commit-config.yaml` SHALL include a hook that runs `mkdocs build --strict` whenever any file under `docs/`, `mkdocs.yml`, or `requirements-docs.txt` is staged. The hook MUST use the project's pinned `mkdocs-material` and related dependency versions.

#### Scenario: Staged docs change triggers build

- **WHEN** a contributor stages a change to a file under `docs/` and runs `git commit`
- **THEN** the pre-commit hook invokes `mkdocs build --strict` and the commit fails if the build fails

#### Scenario: Non-docs change skips docs build

- **WHEN** a contributor stages only Rust or Python source changes (no files under `docs/`, `mkdocs.yml`, or `requirements-docs.txt`)
- **THEN** the docs build hook is not invoked
