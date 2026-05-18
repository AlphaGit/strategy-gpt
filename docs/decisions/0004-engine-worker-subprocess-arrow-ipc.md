# 0004 — Engine workers via subprocess + Arrow IPC

## Context

Strategies — including LLM-emitted ones — execute against streams of bars and must be isolated from the orchestrator. The engine control plane (in trusted Rust) needs to launch many strategy runs in parallel, ship per-bar data to each worker, and collect results (trades, signals, equity curves) back. We want predictable resource caps per run and a way to kill a misbehaving worker without affecting siblings.

## Decision

Each backtest runs in a dedicated `engine-worker` subprocess that loads the compiled strategy `cdylib` via `libloading` and drives the strategy's lifecycle over bars. The control plane communicates with workers over Arrow IPC streams (bars in, results out). Resource caps (wall-clock, memory) are enforced per worker.

## Consequences

- Crash, OOM, panic, or runaway-loop in a strategy kills exactly one worker. The orchestrator and sibling workers are unaffected.
- Arrow IPC keeps the per-bar marshaling cost low; columnar layout matches both Rust and Python ergonomics for downstream analysis.
- Adds subprocess-launch latency per run. Acceptable because runs are batched and the launch cost amortizes over thousands of bars.
- Operating-system primitives (process groups, `prlimit`, `setrlimit`) carry the resource enforcement; behavior is consistent across Linux and macOS but Windows is out of scope.

## Alternatives Considered

- **Threads inside the engine.** Cannot isolate a panic and cannot enforce per-strategy memory caps. A miscompiled strategy could take the whole engine down.
- **WASM strategies.** Considered for sandboxing, but cost-prohibitive for the per-bar throughput target and forces a more constrained strategy programming model.
- **Pipes + custom binary protocol.** Hand-rolling a schema beats Arrow only on raw bytes per bar; the schema-evolution and zero-copy properties of Arrow are worth the dependency.

## Status

accepted
