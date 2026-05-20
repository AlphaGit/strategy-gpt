## ADDED Requirements

### Requirement: PROMPT_API.md as authoritative LLM context

The `engine-rt` crate SHALL ship a hand-maintained `crates/engine-rt/PROMPT_API.md` document that is the single source of truth for hypothesize generate prompts. The document MUST contain: the full `Strategy` trait signature with lifecycle ordering, the complete `Context` capability handle surface (every method, return type, side effect, when callable), all data types reachable from strategy code (`Bar`, `Trade`, `Signal`, `RegimeTag`, `BacktestMetrics`, etc.), the current allowed-crate list rendered verbatim from the build-pipeline whitelist, the named param-declaration convention, the file-layout convention (`src/lib.rs` entry point + helpers under `src/`), an explicit list of forbidden constructs (no `unsafe`, no FFI, no network or filesystem access outside `Context`), and a minimal end-to-end exemplar strategy. The Hypothesis Loop's generate prompts SHALL embed this document verbatim in every reasoning call that emits strategy code.

#### Scenario: Hypothesize generate prompt includes the document

- **WHEN** the Hypothesis Loop's `generate_stage3_files` node constructs its prompt
- **THEN** the prompt contains the verbatim contents of `crates/engine-rt/PROMPT_API.md`

#### Scenario: Document evolves with the engine-rt surface

- **WHEN** a new `Context` method is added to `engine-rt`
- **THEN** `PROMPT_API.md` is updated in the same commit so the prompt-visible surface tracks the actual surface

### Requirement: Named param-declaration convention with build-pipeline introspection

Strategies SHALL declare their parameters via a named convention that allows the build pipeline to introspect declared parameter names, types, and bounds from a compiled strategy artifact. The convention MUST be documented in `PROMPT_API.md` and MUST be followed by all strategies (including the reference `vxx-strategy` and the fixture `example-strategy`). The build pipeline SHALL expose a `declared_param_schema(artifact)` surface that returns the introspected schema for consumption by the Tester's `param_intent` validation step.

#### Scenario: Build pipeline reports declared params

- **WHEN** a strategy declares parameters `vol_lo: f64 ∈ [0.001, 0.05]` and `vol_hi: f64 ∈ [0.01, 0.20]` via the declared convention
- **THEN** the build pipeline's `declared_param_schema` surface returns those names, types, and bounds for the compiled artifact

#### Scenario: param_intent referencing an undeclared param is rejected

- **WHEN** a candidate's `param_intent.added` references `hedge_ratio` but the compiled artifact's declared schema does not contain that name
- **THEN** the Tester records `reject_schema` and the repair loop is invoked with the list of declared parameter names
