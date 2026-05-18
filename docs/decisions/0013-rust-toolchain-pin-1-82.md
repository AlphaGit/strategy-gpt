# 0013 — Rust 1.82.0 toolchain pin

## Context

Reproducible builds require a fixed Rust toolchain across all developer machines and CI. Without a pin, contributors silently land on different compiler versions; nightly drift can introduce clippy lint changes or codegen differences that the lint suite cannot catch.

## Decision

The Rust toolchain is pinned to **1.82.0** in `rust-toolchain.toml`. The pre-commit hooks invoke `cargo fmt` and `cargo clippy` via `system` language hooks that respect the pin; pre-commit does not install a parallel toolchain.

## Consequences

- One source of truth for the Rust version: `rust-toolchain.toml`.
- CI, local dev, and pre-commit all agree on the compiler.
- Toolchain bumps are a deliberate change requiring code review (the bump touches `rust-toolchain.toml`, often clippy fixes, and possibly `Cargo.lock`).
- Lags upstream Rust by intent; the bump cadence is "when there's a reason," not "automatic."
- A contributor without rustup uses the pinned version automatically once they install rustup.

## Alternatives Considered

- **Latest stable, floating.** Bites everyone whenever Rust ships clippy refinements; non-reproducible across machines.
- **MSRV (minimum supported version) without a pin.** Useful for libraries; this is an application stack, not a library, so a pin beats a floor.
- **Nightly.** Convenience features not worth the determinism cost.

## Status

accepted
