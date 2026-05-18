# 0001 — Rust for the execution layer

## Context

The system runs many backtests per research-loop iteration. Two properties are non-negotiable for the execution path: deterministic per-bar throughput (the loop's wall-clock cost is dominated by engine work), and memory safety (we will ultimately run LLM-emitted strategy code in a worker process and want zero tolerance for memory corruption escaping to the orchestrator).

## Decision

The execution layer — engine, data gateway, ledger, knowledge base storage, build pipeline, and the strategy ABI — is written in Rust. Strategies themselves are Rust `cdylib` crates loaded by the engine worker.

## Consequences

- Single-digit-microsecond per-bar overhead and predictable allocation behavior.
- Memory safety covers both the trusted core *and* the strategy worker by language guarantee, before we even add the subprocess boundary (see [0004](0004-engine-worker-subprocess-arrow-ipc.md)).
- A polyglot stack (Rust + Python) requires a binding layer — see [0003](0003-pyo3-trusted-crate-boundary.md) — and CI must build both toolchains.
- Strategy authoring carries a higher barrier than scripting languages; mitigated by the build pipeline emitting strategies from LLM output.

## Alternatives Considered

- **Pure Python execution.** Throughput unacceptable at the trial counts the optimizer requires; GIL contention bites once worker parallelism scales.
- **C++ execution.** Comparable throughput but no memory-safety guarantee for the strategy boundary. Toolchain fragmentation (CMake variants, compiler-version skew) worse than Rust's single `cargo` story.
- **Go execution.** Adequate throughput but GC pauses bias per-bar latency in ways the determinism contract cannot absorb, and no first-class `cdylib` story for dynamically-loaded strategies.

## Status

accepted
