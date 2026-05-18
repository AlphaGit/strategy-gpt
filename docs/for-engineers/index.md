# For engineers

Reading path for platform engineers extending strategy-gpt. Read top-to-bottom on your first pass.

## Orient

1. [Architecture](../explanation/architecture.md) — module map, trust boundaries, data flow.
2. [Decisions index](../decisions/index.md) — load-bearing ADRs, especially [0003 (PyO3)](../decisions/0003-pyo3-trusted-crate-boundary.md), [0004 (workers)](../decisions/0004-engine-worker-subprocess-arrow-ipc.md), [0005 (no sandbox)](../decisions/0005-worker-process-isolation-no-sandbox.md), [0006 (sealed trait)](../decisions/0006-sealed-strategy-trait.md).

## Build & verify

3. The README's `README.md#quickstart` — install Rust 1.82, Python, maturin, pre-commit.
4. [ADR 0014 — Lint stance](../decisions/0014-lint-stance.md) — Rust tool-defaults, Python strict ruleset, mypy strict scope.

## Internal contracts

5. [BatchSpec reference (internal)](../reference/batch-spec.md) — engine input across the PyO3 boundary; the experiment-spec loader emits this.
6. Capability specs under `openspec/specs/` — normative requirements every subsystem implements. Specs are the contract for code.

## Methodology, when relevant

7. [Overfitting & selection](../explanation/overfitting-and-selection.md) — useful when changing optimizer internals or extending the selection layer.
