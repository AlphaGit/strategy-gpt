# 0014 — Lint stance: Rust tool defaults, Python strict

## Context

The two halves of the codebase have different needs. Rust is compile-checked; rustfmt and clippy with default settings already catch most of what matters, and overriding their defaults creates per-project bikesheds without proportional payoff. Python lacks compile-time types and is the more error-prone surface; the orchestrator carries the LLM-integration logic where bugs are easy to write and hard to detect.

## Decision

- **Rust** — tool defaults. No `.rustfmt.toml`, no `clippy.toml`. The canonical commands are `cargo fmt --all -- --check` and `cargo clippy --workspace --all-targets -- -D warnings`.
- **Python** — strict. Ruff with rule selection `E,F,W,I,B,UP,SIM,RUF,S,N,PT,ANN,C4,ERA,PL` plus `ruff format --check` plus `mypy --strict`. The `mypy --strict` scope is `python/strategy_gpt/`; tests and `kb/` ingestion scripts are excluded explicitly.
- Single entry point: `make lint`. Pre-commit and CI both call it.

## Consequences

- Rust contributors do not bikeshed style — the tool is the policy.
- Python contributors get strong type-narrowing, security smell detection (`S`), naming consistency (`N`), import hygiene (`I`), and modern-Python idioms (`UP`). Mistakes that would only surface at runtime in a permissive setup are caught at lint.
- Strict Python lint occasionally requires explicit annotations or `# noqa: <rule>` justifications. Friction-positive: the comment-of-record is what reviewers want.
- Tool versions are pinned in `.pre-commit-config.yaml`; tool drift requires a deliberate update.

## Alternatives Considered

- **Permissive Python lint.** Rejected. The orchestrator's bug surface (FFI types, prompt payloads, optimizer state) is precisely where strict typing pays off.
- **Custom Rust style.** Rejected. We have nothing to gain that justifies the perpetual review cost of a `.rustfmt.toml`.
- **`mypy` strict everywhere.** Rejected for ingestion scripts under `kb/` where adapter shapes change frequently and the productivity cost outweighs the benefit.

## Status

accepted
