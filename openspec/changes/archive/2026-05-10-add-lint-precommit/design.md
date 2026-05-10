## Context

Polyglot repo: Rust workspace under `crates/`, Python orchestrator under `python/strategy_gpt/`. No lint enforcement currently runs locally or in CI. The rewrite-architecture change will eventually add CI (task 13.3); we want lint gates ready before that lands so CI just calls into them rather than reinventing checks.

Two languages with different cultural norms:
- **Rust** has battle-tested defaults. `rustfmt` defaults are universally accepted; `clippy` default lints catch real bugs without nagging. We don't customize.
- **Python** defaults are weak (PEP 8 is a baseline, not a ceiling). To get strong guarantees we configure aggressively: many rule families, type strictness, format enforcement.

The asymmetry is intentional: idiomatic Rust written against the type system rarely benefits from extra rules; idiomatic Python written without aggressive linting silently rots.

## Goals / Non-Goals

**Goals:**

- A single command (`pre-commit run --all-files` or `make lint`) gates every commit and every CI run on the same checks.
- Rust: format check + clippy with defaults. No bespoke rule allow/deny lists.
- Python: format check + lint with a wide rule selection + strict typing. Strict means `mypy --strict` (or equivalent flags) and `ruff` rule set covering correctness, style, security, and complexity.
- Failures are reproducible: hooks pin tool versions so a green local run matches CI.
- Friction is bounded: `pre-commit` runs only on staged files by default; full-tree runs are opt-in.

**Non-Goals:**

- Editor integration (handled by user via their own editor config).
- Auto-formatting unrelated files in a commit (`pre-commit` runs on staged files; if a hook auto-fixes, it stages the fix and the commit must be re-attempted — standard `pre-commit` behavior).
- Commit-message linting, license headers, secrets scanning, dependency-vuln scanning — separate concerns, separate changes.
- Custom Python style overrides (e.g., line length 120, double quotes preferred). We accept ruff's defaults except where strict requires changes.

## Decisions

### Decision 1: Rust uses tool defaults

**Choice:** No `.rustfmt.toml`. No `clippy.toml`. Use whatever the pinned toolchain (1.82) ships. Run `cargo fmt --all -- --check` and `cargo clippy --workspace --all-targets -- -D warnings`.

**Why:** Rust's default style is widely adopted and rarely contested. Customizing invites bikeshedding without measurable benefit. `clippy::all` (the default) catches real issues; `-D warnings` makes the gate hard. Adding `clippy::pedantic` or `clippy::nursery` produces too many false positives for too little gain on a small codebase.

**Alternatives considered:**

- *Add `clippy::pedantic`.* Rejected: noisy, slows iteration, marginal value.
- *Custom `rustfmt.toml` (e.g., `imports_granularity`, `group_imports`).* Rejected: not enough volume to justify a style fork.

### Decision 2: Python uses ruff (lint + format) and mypy strict

**Choice:**

- `ruff check` enforces a wide rule selection. Initial set: `E`, `F`, `W` (pycodestyle/pyflakes), `I` (import sorting), `B` (bugbear), `UP` (pyupgrade), `SIM` (simplify), `RUF` (ruff-specific), `S` (bandit security), `N` (naming), `PT` (pytest style), `ANN` (annotations), `C4` (comprehensions), `ERA` (eradicate commented-out code), `PL` (pylint subset). No project-specific overrides at start; ignores added only when a rule conflicts with a deliberate decision.
- `ruff format --check` enforces formatting. Ruff format is Black-compatible.
- `mypy --strict` over `python/strategy_gpt/` (already declared as `strict = true` in `pyproject.toml`). All public APIs must be fully annotated.

**Why:** Ruff is the fastest tool, replaces Black + isort + pyflakes + pylint (partial) + bandit (partial) in one binary, and is now the de-facto standard. Mypy strict catches the bugs that the runtime doesn't. Together they form a tight loop.

**Alternatives considered:**

- *Black + isort + flake8 + pylint.* Rejected: four tools, slower, fragmented config.
- *Pyright instead of mypy.* Rejected: mypy strict is sufficient and integrates better with `pre-commit`; Pyright's strict mode is similar but adds Microsoft-specific quirks.
- *Looser ruff rule set.* Rejected: user explicitly asked for strict Python.

### Decision 3: Pre-commit framework, version-pinned hooks

**Choice:** Use the `pre-commit` framework (`pre-commit-hooks.com`). Pin tool versions in `.pre-commit-config.yaml`. Hooks: `cargo fmt --check`, `cargo clippy`, `ruff check`, `ruff format --check`, `mypy`, plus baseline `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-toml`, `check-added-large-files`.

**Why:** `pre-commit` is the mainstream framework, version-pins tools per-repo so team members run identical checks regardless of host install. Pinning prevents "works on my machine" lint drift.

**Alternatives considered:**

- *Husky / lefthook / git native hooks.* Rejected: pre-commit is standard for polyglot repos and has the deepest hook ecosystem.
- *No framework, custom shell script.* Rejected: loses version pinning and per-file scoping.

### Decision 4: Single entry point — `make lint`

**Choice:** A root `Makefile` with targets `lint`, `fmt`, `test`, `lint-rust`, `lint-python`. `make lint` runs the same suite as `pre-commit run --all-files` so contributors and CI use one command.

**Why:** A `Makefile` is universally available; no extra tool install. The suite stays callable from CI without invoking `pre-commit` (some CI environments handle pre-commit awkwardly with caches).

**Alternatives considered:**

- *`justfile`.* Rejected for now: extra tool to install. Could swap later.
- *Only `pre-commit run --all-files`.* Rejected: ties contributors to the framework for ad-hoc runs.

### Decision 5: Rust hooks shell out to local cargo, not pre-commit-rs

**Choice:** The Rust hooks use `system` language in `pre-commit` config and shell out to the contributor's pinned cargo toolchain (managed by `rust-toolchain.toml`). Do not use a `pre-commit-rs` mirror.

**Why:** `rust-toolchain.toml` already pins Rust to 1.82. Mirroring rustfmt/clippy via pre-commit's bring-your-own-tool mechanism would add a parallel version source. One source of truth.

### Decision 6: Type checker scope

**Choice:** `mypy --strict` runs on `python/strategy_gpt/` only. Tests under `python/tests/` (when they land) get `mypy` without strict (less annotation pressure on test fixtures). Ingestion scripts under `kb/` are excluded until they stabilize.

**Why:** Strict typing pays off on the orchestrator's public API surface; test code benefits less.

## Risks / Trade-offs

- **Risk: First run produces a large violation list** because the orchestrator scaffolding has no annotations yet. → *Mitigation:* this change includes a "fix violations" task; the suite must be green before merge.
- **Risk: Strict mypy slows iteration on prototyping.** → *Mitigation:* contributors can use `# type: ignore[reason]` with a stated reason for legitimate cases; ruff rule `PGH003` (blanket-type-ignore) catches abuse.
- **Risk: Pre-commit hook runtime grows annoying.** → *Mitigation:* hooks scope to staged files by default; clippy is the slowest, but `cargo clippy` with sccache + incremental builds keeps it under a few seconds on small diffs.
- **Risk: Pinned hook versions drift behind upstream security/bug fixes.** → *Mitigation:* `pre-commit autoupdate` is run quarterly (manual, not automated here).
- **Trade-off: No clippy::pedantic.** Some valuable hints missed. Acceptable given default-only stance and project size.
- **Trade-off: Strict mypy + ruff ANN raises annotation overhead.** Worth it for the orchestrator's stability; revisit if it becomes a friction point.

## Migration Plan

1. Land tool configs (pyproject.toml updates, .pre-commit-config.yaml, Makefile).
2. Run the full suite once and capture the initial violation count.
3. Fix violations in a focused pass.
4. Enable `pre-commit install` in dev docs.
5. Add `make lint` to the future CI workflow (task 13.3 in rewrite-architecture).

No rollback: this is a process change, not a runtime change. Disabling the hooks is one `pre-commit uninstall` away if needed.

## Open Questions

- **Line length.** Ruff defaults to 88 (Black-compatible). Current `pyproject.toml` says 100. Keep 100 or move to 88? Recommendation: keep 100 (already declared, modest improvement in screen real-estate for type signatures); revisit if the team grows.
- **Ignoring docstring rules (D family).** Add `D` rules later when public API stabilizes. Initial set excludes docstring rules to avoid blocking on incomplete docs.
- **Pyrightconfig vs mypy.ini.** Sticking with mypy under `[tool.mypy]` in `pyproject.toml`. Revisit only if mypy proves insufficient.
- **Rust additional checks: cargo deny, cargo audit.** Out of scope here; add as a follow-up `add-supply-chain-checks` change.
