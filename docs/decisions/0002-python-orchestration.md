# 0002 — Python for orchestration

## Context

The orchestrator drives the LangGraph hypothesis loop, the tester pipeline, the parameter optimizer, the CLI surface, and integration with reasoning-model providers (Anthropic, OpenAI). It is glue-and-coordination code, not throughput-critical.

## Decision

The orchestrator is written in Python. LangGraph, LangChain, pydantic, and the project's CLI (Typer) form the toolkit. PyO3 bindings expose the trusted Rust crates to Python as `strategy_gpt._native` (see [0003](0003-pyo3-trusted-crate-boundary.md)).

## Consequences

- First-class access to the LangChain / LangGraph ecosystem, which leads observability tooling, prompt-engineering libraries, and provider abstractions.
- pydantic gives runtime schema validation matching the Rust serde types one-to-one (`python/strategy_gpt/types.py`).
- Two toolchains in CI; mitigated by `make lint` and `make test` exposing one entry point.
- mypy strict + ruff strict ruleset compensate for the absence of compile-time types.

## Alternatives Considered

- **Rust orchestrator.** Would eliminate the FFI boundary but lose the LLM-tooling ecosystem (LangGraph, observability) and the prompt-iteration ergonomics that matter most at the hypothesis-loop seam.
- **TypeScript orchestrator (Node).** LangChain.js exists but lags Python in maturity, and the audience overlap with research/quant tooling is narrower.

## Status

accepted
