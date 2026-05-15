# Spec: hypothesis-loop

## Purpose

LangGraph-orchestrated reasoning loop that diagnoses backtest results, queries the knowledge base, generates and self-critiques candidate hypotheses, ranks them, and persists an accept/reject decision log. Every accepted hypothesis carries citations and a falsification criterion that the Tester can verify against backtest output.

## Requirements

### Requirement: LangGraph workflow with explicit nodes

The Hypothesis Loop SHALL be implemented as a LangGraph workflow with the following nodes: `diagnose`, `kb_query`, `generate`, `critique`, `rank`, `select`. State transitions between nodes MUST be explicit and observable.

#### Scenario: Workflow executes node sequence

- **WHEN** a backtest result is submitted to the loop
- **THEN** `diagnose` runs first, followed by `kb_query`, `generate`, `critique`, `rank`, and `select` in that order, with state visible at each transition

### Requirement: Internal iteration loop

The workflow SHALL loop through `generate` → `critique` → `rank` until at least K hypotheses pass critique, an iteration budget is exhausted, or candidate similarity to prior items crosses a configured threshold. The termination reason MUST be recorded.

#### Scenario: Loop terminates on sufficient candidates

- **WHEN** the critique node has accepted K hypotheses
- **THEN** the loop exits and `select` proceeds with the accepted set

#### Scenario: Loop terminates on budget exhaustion

- **WHEN** the iteration budget is reached without K accepted hypotheses
- **THEN** the loop exits with the partial accepted set and records `terminated: budget_exhausted`

### Requirement: Knowledge base queries with citation capture

The `kb_query` node SHALL retrieve relevant concepts, indicators, regimes, models, and techniques from the Knowledge Base and attach citations to each generated hypothesis. Citations MUST include source provenance (book, page or paper, section).

#### Scenario: Hypothesis carries citations

- **WHEN** the `generate` node produces a hypothesis informed by a KB retrieval
- **THEN** the hypothesis record contains a list of `kb_cites` with source provenance

### Requirement: Decision log persistence

Every accepted hypothesis SHALL be persisted with rationale, evidence, KB citations, and timestamp. Every rejected hypothesis SHALL be persisted with its rejection reason and timestamp. The decision log MUST be stored in the experiment ledger and re-loaded as context on subsequent runs.

#### Scenario: Past rejected ideas inform future rejections

- **WHEN** a new hypothesis closely resembles a previously rejected one
- **THEN** the `critique` node reads the prior rejection rationale from the ledger and accounts for it

### Requirement: Hypothesis output schema

Each hypothesis emitted to the Tester SHALL include: a human-readable name, the metric it intends to improve, a falsification criterion (the threshold or sign of metric movement that would make the hypothesis false), the proposed change (parameter diff or new strategy source intent), KB citations, and an estimated lift confidence.

#### Scenario: Tester receives a fully specified hypothesis

- **WHEN** the loop emits a hypothesis to the Tester
- **THEN** the hypothesis record contains all required fields and the Tester can decide acceptance/rejection without further input from the loop

### Requirement: Reasoning model usage

The `critique` and `diagnose` nodes SHALL use a reasoning-capable model. The model is configurable but the workflow MUST default to the most capable reasoning model available at runtime.

#### Scenario: Configured model is honored

- **WHEN** the workflow is configured to use a specific model
- **THEN** all reasoning calls in `critique` and `diagnose` use that model
