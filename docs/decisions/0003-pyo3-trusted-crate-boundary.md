# 0003 — PyO3 in-process boundary for trusted crates only

## Context

The Python orchestrator must call into the Rust core (engine control plane, gateway, ledger, kb, build pipeline) often and with low overhead. At the same time, the project's threat model treats strategy code authored by an LLM as untrusted: a panic, memory bug, or runaway loop in a strategy must not corrupt or crash the orchestrator.

## Decision

PyO3 hosts an in-process Rust extension (`strategy_gpt._native`) exposing **only** trusted crates the team owns and reviews. LLM-emitted strategy code never crosses the PyO3 boundary; it executes in a separate worker process (see [0004](0004-engine-worker-subprocess-arrow-ipc.md)).

## Consequences

- Calls into the engine control plane, ledger reads, KB retrieval, and dataset cache lookups are zero-copy and microsecond-scale.
- The trust boundary is enforced by *which crates we bind*, not by any runtime sandbox. Code review of crate additions is therefore a security gate.
- Build complexity: `maturin develop` is part of onboarding, and the project pins `maturin` versions explicitly.
- A panic in trusted Rust still crashes the Python process; we accept this because trusted code is reviewed.

## Alternatives Considered

- **Subprocess for everything.** Adds per-call serialization cost the hot paths cannot absorb (ledger writes, KB queries).
- **gRPC / IPC for trusted crates.** Same overhead as subprocess, plus added deployment surface.
- **PyO3 with strategies bound too.** Rejected — a strategy bug would take down the orchestrator. The worker-subprocess boundary exists precisely to prevent this.

## Status

accepted
