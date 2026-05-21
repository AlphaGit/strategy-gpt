# The hypothesize loop

Understanding-oriented page on *why* the hypothesis loop is shaped the way
it is. Extracted from the design doc that drove the
`expand-hypothesis-loop-to-logic-search` change.

## What the loop is for

Strategy-GPT's reason for existing is the loop: hypothesis → code →
backtest → verdict → next hypothesis. The hypothesize subsystem generates
*strategy-logic ideas* — new components, replacement subsystems,
structural mutations — informed by how the current strategy behaves,
what the curated KB contains, and what has been tried before.

Parameter tuning is a separate subsystem (`optimize`). The hypothesize
loop is the logic-search counterpart.

## Shape of one iteration

```
diagnose → kb_query → kb_filter → [inner loop:
    generate_stage1_idea → cheap_critique →
    generate_stage2_commitments → generate_stage3_files →
    build_and_smoke → mini_optimize →
    mechanical_gate → verdict_critique → rank →
    should_continue?
] → select
```

Every node is a real LangGraph `StateGraph` node so the workflow can be
inspected, paused, and resumed. State transitions are explicit; nodes
are pure functions of state plus injected collaborators.

## Why multi-stage emission

LLMs struggle to produce a single well-formed omnibus markdown response
combining structured metadata, multiple code files, and free-form
rationale. Splitting emission into three focused calls — each receiving
the prior stage's well-formed output as locked context — improves
per-call quality and reduces repair frequency.

- **Stage 1** commits to the idea: name, rationale, lift confidence,
  expected side effects.
- **Stage 2** commits to a measurable claim and parameter bounds.
- **Stage 3** implements against those commitments.

Locking earlier stages prevents the model from drifting on its own
commitments mid-repair ("rewrite the falsification claim to dodge a
build error"). Repair budget is per-stage; an earlier stage's
commitments are immutable once accepted. See
[ADR 0019](../decisions/0019-multi-stage-llm-emission.md).

## Why a variance-aware mechanical gate

A naive accept threshold ("candidate aggregate > baseline aggregate") is
wrong in two directions. It is **too permissive** under high per-fold
variance, and **too restrictive** for candidates that move metrics the
LLM did not claim.

The mechanical gate addresses the first failure mode deterministically:

```
σ_combined = sqrt(σ_candidate² + σ_baseline²)
accept iff (cand - baseline) > k · σ_combined
        AND fold_cv < threshold
```

The gate is a **hard floor**. No downstream node, including the LLM
verdict-critique, may reverse it. A genuinely strong but borderline
candidate is allowed to re-emerge in a later iteration with cleaner
evidence. See
[ADR 0020](../decisions/0020-comparative-falsification-variance-aware-epsilon.md).

## Why per-strategy storage

Each strategy accumulates its own history of decisions, source blobs,
and LLM responses, isolated from siblings:

```
ledger/strategies/<strategy>/
  hypothesis_records.parquet
  decision_records.parquet
  baseline/best.json
  sources/<files_set_hash>/{Cargo.toml, src/lib.rs, ...}
  responses/<decision_id>/{stage1_idea.md, stage2_commitments.md, ...}
```

Source bundles are content-addressed — identical baseline source across
candidates deduplicates naturally. Replay reconstructs files from blobs
and recompiles via the build-pipeline's content-addressed artifact
cache. See [ADR 0017](../decisions/0017-per-strategy-storage-layout.md).

## Why simplicity-preferring rank

Two candidates with identical measured lift and citation count — the
simpler one wins. The rank score includes a continuous complexity
differential (`delta_params + delta_components`) plus an explicit
bonus for net removals:

```
rank = 0.55 · lift
     + 0.25 · evidence
     - 0.15 · max(0,  delta_components + delta_params)   # penalize net additions
     + 0.05 · max(0, -delta_components - delta_params)   # reward net removals
```

This grounds the loop's bias toward minimal candidates without making
removal mandatory.

## Why the cheap-critique fires after stage 1

Idea-level rejection (duplicate of prior reject, contradicts diagnosis,
violates prior accept) requires only the idea text and prior decisions.
Running cheap-critique after stage 1 saves stage 2/3 LLM cost on
candidates that would die anyway.

## Why the loop does not own the engine

The orchestrator depends on a `HypothesizeDeps` bag carrying a
`evaluate_fold(params, fold_idx) -> BacktestMetrics` callable. The
hypothesize subsystem treats the engine as a black box: it asks for a
metric surface, the operator wires it. Tests inject a deterministic
function; production wires it to a small engine batch through the
trusted Rust crates.

## Replay guarantees

Identical baseline result + recorded candidate source bundle + recorded
mini-optimize seed = byte-identical replay. The exception is engine-rt
evolution: candidates compiled against an older PROMPT_API surface may
fail to recompile after engine-rt evolves. The project has chosen not
to support multi-version replay ([ADR 0018](../decisions/0018-no-versioning-on-hypothesis-records.md)).

## Non-goals

- **No CSCV/PBO overfit detection inside hypothesize.** Sample size (K
  candidates × iterations × folds) is too small for these statistics to
  be meaningful. Overfit detection remains the job of downstream
  `optimize`.
- **No live trading, broker integration, or position management** as a
  product feature.
- **No dependency review workflow.** The allowed-crate whitelist is
  strict; "dependency suggestions" are not collected or surfaced.

## See also

- [How to run hypothesize](../how-to/run-hypothesize.md)
- [Hypothesize CLI reference](../reference/hypothesize-cli.md)
- [ADR 0016 — PROMPT_API.md authoritative LLM context](../decisions/0016-prompt-api-md-authoritative-llm-context.md)
- [ADR 0017 — per-strategy storage layout](../decisions/0017-per-strategy-storage-layout.md)
- [ADR 0018 — no versioning on hypothesis records](../decisions/0018-no-versioning-on-hypothesis-records.md)
- [ADR 0019 — multi-stage LLM emission](../decisions/0019-multi-stage-llm-emission.md)
- [ADR 0020 — comparative falsification with a variance-aware epsilon](../decisions/0020-comparative-falsification-variance-aware-epsilon.md)
