# 0005 — Worker process isolation, no in-process sandboxing

## Context

LLM-emitted strategy code is treated as untrusted. The threat model is *bug containment*, not *adversarial defense* — we are not protecting against a sentient adversarial strategy, only against panics, OOMs, runaway loops, and accidental capability misuse. The `Context` capability handle (see `openspec/specs/strategy-runtime/spec.md`) intentionally exposes no filesystem, network, syscall, broker, or order-cancellation surface; everything I/O-shaped must route through `Context`.

## Decision

The safety boundary for strategy code is the operating-system process boundary alone. There is no in-process sandbox (no seccomp filters, no namespace isolation beyond what the OS gives the subprocess, no WASM, no language-level capability monad). A worker that wants to misbehave can issue any syscall the user account has access to. Per-worker resource caps (wall-clock, memory) and the absence of capability handles in `Context` are the only mitigations.

## Consequences

- Implementation simplicity: workers are ordinary subprocesses launched by the engine control plane.
- Portability: no Linux-only kernel features required.
- A strategy *could* perform arbitrary I/O via raw syscalls. Accepted because the build-pipeline's allowed-crate whitelist and source linter (`crates/build-pipeline/whitelist.toml`) gate which crates LLM-emitted code can pull in; reaching for raw syscalls requires `unsafe` and a crate that exposes them, both of which the linter rejects.
- If the threat model evolves to require adversarial defense, this ADR will be superseded by a new one that introduces seccomp/landlock/namespace isolation.

## Alternatives Considered

- **seccomp-bpf filters.** Linux-only; complicates the macOS dev experience; not warranted by the current threat model.
- **WASM strategies.** Adds language-runtime overhead and forecloses Rust's full library ecosystem for human-authored strategies. Considered if the threat model tightens.
- **Capability-passing in-process sandbox.** No mature Rust solution that wouldn't reintroduce orchestrator-crash risk.

## Status

accepted
