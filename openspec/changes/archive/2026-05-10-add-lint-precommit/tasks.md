## 1. Tool Configuration

- [x] 1.1 Update `python/pyproject.toml`:
    - Replace the existing minimal `[tool.ruff.lint]` selection with the strict set: `E`, `F`, `W`, `I`, `B`, `UP`, `SIM`, `RUF`, `S`, `N`, `PT`, `ANN`, `C4`, `ERA`, `PL`
    - Add `[tool.ruff.format]` (defaults; ruff format is Black-compatible)
    - Confirm `[tool.mypy]` has `strict = true`, `python_version = "3.11"`, and add `[[tool.mypy.overrides]]` entries excluding `kb.*` and (when present) test paths from strict
    - Add `pre-commit>=4`, `ruff>=0.7`, `mypy>=1.13` to the `dev` optional-dependencies group if not already present
- [x] 1.2 Verify Rust uses defaults: confirm absence of `.rustfmt.toml` and `clippy.toml`; do NOT create them
- [x] 1.3 Confirm `rust-toolchain.toml` pins a stable channel that ships compatible `rustfmt` and `clippy` (already pinned to 1.82.0)

## 2. Pre-commit Configuration

- [x] 2.1 Create `.pre-commit-config.yaml` at the repo root with version-pinned hooks
- [x] 2.2 Wire baseline hygiene hooks: `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-toml`, `check-added-large-files`, `mixed-line-ending`
- [x] 2.3 Wire ruff check (`--fix` disabled in CI mode, enabled locally), ruff format `--check`, mypy
- [x] 2.4 Wire `cargo fmt --all -- --check` and `cargo clippy --workspace --all-targets -- -D warnings` as `system`-language hooks that shell out to the local toolchain
- [x] 2.5 Verify `pre-commit run --all-files` runs cleanly against a clean checkout (will fail until task 4 lands; expected for the first run)

## 3. Unified Entry Point

- [x] 3.1 Create a root `Makefile` with targets:
    - `lint` — runs the full suite (Rust fmt+clippy, Python ruff check + ruff format --check + mypy)
    - `fmt` — runs Rust+Python formatters (writing changes, not checking)
    - `lint-rust` — Rust gates only
    - `lint-python` — Python gates only
    - `test` — placeholder that calls `cargo test --workspace` and (later) `pytest`; returns success if no Python tests yet
- [x] 3.2 Document each target in the Makefile via a `help` target

## 4. Bring the Tree to Green

- [x] 4.1 Run `make lint`; capture violation counts per tool
- [x] 4.2 Fix Rust violations (expected: zero or near-zero given clippy was already clean)
- [x] 4.3 Fix Python ruff lint violations on `python/strategy_gpt/` (annotations, imports, security)
- [x] 4.4 Apply `ruff format` to `python/strategy_gpt/`
- [x] 4.5 Add type annotations to satisfy `mypy --strict` over `python/strategy_gpt/`
- [x] 4.6 Confirm `make lint` exits zero from a clean checkout

## 5. Documentation

- [x] 5.1 Update `CLAUDE.md` with a "Lint and pre-commit" section: one-time setup (`pre-commit install`), canonical commands, scope of strict typing
- [x] 5.2 Update `python/README.md` with the same setup snippet
- [x] 5.3 Add a top-level `README.md` snippet (or `CONTRIBUTING.md`) documenting `make lint` and `make fmt`

## 6. CI Hand-off

- [x] 6.1 Leave a TODO comment in the `Makefile` and the `rewrite-architecture` change's task 13.3 noting that CI calls `make lint`; do not author the CI workflow file in this change
