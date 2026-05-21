## Context

Strategy-GPT's reason for existing is the loop: hypothesis → code → backtest → verdict → next hypothesis. The current `hypothesis-loop` spec and partial implementation treat that loop as "diagnose one backtest and propose tweaks." The user-facing demand is broader: the engine must generate *strategy-logic ideas* — new components, replacement subsystems, structural mutations — informed by the strategy's behavior, the curated KB, and the history of past decisions. Parameter tuning belongs to `optimize`. Logic search is the missing distinct loop.

The existing partial implementation provides useful building blocks:

- `diagnose.py` produces a structured `Diagnosis` from a `BacktestResult` (trade stats, regime performance, signal misfires, exec-log summary).
- `kb_query.py` retrieves citations from a `KbClient` and projects them to `KbCitation` records.
- `nodes.py` provides pure-function `generate / critique / rank / select` nodes plus a `run_inner_loop` driver.
- `hypothesis_loop.py` defines the state schema, `persist_decisions`, and `bootstrap_state_from_ledger`.
- `reasoning.py` defines the model selector and `HypothesisLoopConfig`.
- `tester.py` has translate / verdict / attempt_logic_change helpers.

What is missing or misshapen:

- No real LangGraph `StateGraph` — the spec mandates one but nodes are plain function calls.
- No real reasoning client — only a `Protocol` and stub in `smoke.py`.
- No candidate compilation, no in-loop backtests, no comparative falsification, no repair.
- No per-strategy persistence — current ledger is flat.
- CLI `hypothesize` is a stub.

This design specifies how to grow the building blocks into a strategy-logic search loop. Multi-stage LLM emission, per-candidate mini-optimization, mechanical gate with variance-aware ε, verdict-critique, simplicity-preferring rank, and per-strategy storage are the new structural pieces. The rewrite avoids greenfield: existing modules extend in place.

## Goals / Non-Goals

**Goals:**

- The loop generates *strategy-level* logic ideas (add components, replace subsystems, simplify), not parameter tweaks.
- Each candidate is compiled, smoke-tested, mini-optimized over its declared parameter bounds, and validated against a variance-aware comparative falsification of its own claim plus optional guard constraints.
- All accepted and rejected decisions persist with rationale, evidence, KB citations, and a content-addressed source-blob reference, isolated per strategy under `ledger/strategies/<strategy_name>/`.
- LLM emissions are produced in three locked stages (idea+rationale → commitments → files), with focused prompts to improve per-stage quality and reduce repair cost.
- Repair loop scoped to one stage at a time with `K_repair = 2`; never re-opens an earlier stage's commitments.
- The mechanical gate is a non-negotiable hard floor — LLM verdict-critique cannot override it.
- Replay is byte-identical given the same baseline result, candidate file blobs, and recorded mini-optimize seed.
- The loop is configurable via CLI flags but operates with sensible defaults out of the box.

**Non-Goals:**

- No live trading, broker integration, or position management as a product feature.
- No CSCV/PBO overfit detection inside hypothesize; sample size (K × iterations) is too small to make those statistically meaningful. Overfit detection remains the job of downstream `optimize`.
- No runner-version pinning on hypothesis records and no multi-version engine-rt support. Engine-rt is a moving target; the design accepts that replay against newer engine-rt may fail.
- No dependency review workflow. Allowed-crate whitelist is strict; "dependency suggestions" are not collected or surfaced.
- No probe-suite planner. Multiple backtests in a hypothesize run come naturally from per-candidate validation, not from a separate "diagnostic probe" phase.
- No LangGraph rewrite for capabilities outside hypothesis-loop. Other workflows remain as they are.

## Decisions

### Decision: Drop the probe-suite as a separate planner; rely on rich diagnose + per-candidate validation

The earlier design space included a "probe-suite source" question (fixed slate vs. spec-declared vs. heuristic vs. LLM-planned vs. hybrid). The user clarified that the engine should produce *strategy-logic ideas*, not slice the data into more diagnostic angles. Ideas of the form "add a hedging leg" or "replace stop-loss with trailing" do not benefit from running the baseline strategy on different data slices; they require LLM ideation against (a) the strategy source, (b) a rich single-baseline diagnosis, and (c) the curated KB.

**Alternative considered**: heuristic-planned probes + ledger reuse. Rejected because no design-space alternative actually feeds the *generative* step better than the strategy source + a single rich diagnosis. The probe planner introduced complexity, cost, and replay state without a clear payoff for the user's stated goal.

The "multiple backtests" the engine still runs come from per-candidate mini-optimization and fold execution inside the tester. That is sufficient and aligns with how `optimize` already operates.

### Decision: Multi-stage LLM emission (idea+rationale → commitments → files), locked progression

LLMs struggle to produce a single well-formed omnibus markdown response combining structured metadata, multiple code files, and free-form rationale. Splitting emission into three focused calls — each receiving the prior stage's well-formed output as locked context — improves per-call quality and reduces repair frequency. The extra LLM cost (3 calls per candidate vs. 1) is negligible compared to the mini-optimize cost downstream.

Locking earlier stages prevents the model from drifting on its own commitments mid-repair ("rewrite the falsification claim to dodge a build error"). Stage 1 commits to the idea, Stage 2 commits to the measurable claim and parameter bounds, Stage 3 implements against those commitments. Repair budget is per-stage; an earlier stage's commitments are immutable once accepted.

**Alternative considered**: single omnibus emission. Rejected because LLM-emitted JSON with embedded Rust code and structured fields is fragile; quoting / escaping failures dominate the rejection surface.

### Decision: Markdown emit + parse contract (not JSON)

LLMs encode markdown with code-fenced blocks reliably. JSON containing multi-line Rust source forces nested quoting that LLMs frequently get wrong. The Stage 3 emission is therefore markdown — `## <path>` H2 headers followed by fenced code blocks. Structured metadata (falsification, param_intent) uses YAML inside fenced blocks (LLMs handle YAML well; multiline strings without escape rules).

**Alternative considered**: JSON files map (`{path: content}`). Rejected because of escape fragility on real-world Rust source emissions.

### Decision: Cheap-critique runs after Stage 1, not after Stage 3

Idea-level rejection (duplicate of prior reject, contradicts diagnosis, malformed claim) requires only the idea text and prior decisions. Running cheap-critique after Stage 1 saves Stage 2/3 LLM cost on candidates that would die anyway.

**Alternative considered**: post-Stage 3 cheap-critique. Rejected because it pays for two LLM calls (Stage 2 + Stage 3) before any cheap rejection can fire.

### Decision: Mini-optimize per candidate over LLM-supplied bounds

The user explicitly endorsed exhaustive evaluation. Each surviving candidate runs a mini-optimize pass (default sobol, 64 trials) over the LLM-declared `param_intent.bounds` for added params and the experiment-spec bounds for kept params. Removed params are absent from the search space entirely. Folds come from the experiment-spec.

Both baseline-best and candidate-best are optimized inputs, so falsification compares apples to apples.

**Alternative considered**: single-point evaluation with LLM-suggested params (option B). Rejected — risks rejecting good logic at bad params. The user prioritized evaluation fidelity over per-iteration cost.

**Alternative considered**: tiered cheap-then-expensive (option D). Rejected for added complexity (borderline-detection heuristic introduces another tuning surface) and unclear benefit when engine throughput is acceptable.

### Decision: Mechanical gate is a hard floor; LLM verdict-critique cannot override

Two gate checks, both deterministic:

```
σ_combined = sqrt(σ_candidate² + σ_baseline²)
accept score floor:    (cand - baseline) > k · σ_combined
accept variance floor: fold_cv < threshold
```

`k` defaults to 1.0 (configurable). `fold_cv` defaults to 0.5. Both must pass. Borderline-pass propagates as a flag to verdict-critique so the LLM applies stricter scrutiny on marginal candidates, but it cannot reverse a gate rejection.

**Why no override path**: if the gate could be overridden by an LLM call, the gate stops being a floor. The variance-aware check is the only statistically motivated reject in the loop; weakening it costs more than it saves. A genuinely strong but borderline candidate will re-emerge in a later iteration with cleaner evidence.

### Decision: Comparative falsification with primary claim + guard constraints + scope

Each candidate carries an LLM-stated falsification:

```yaml
primary:
  metric: objective_score
  direction: gt
  delta_vs_baseline: 0.20
  scope: aggregate
guard_constraints:
  - { metric: max_drawdown, direction: lte, delta_vs_baseline: 0.05 }
  - { metric: trade_count,  direction: gte, factor: 0.5 }
```

Primary claim is required. Guard constraints are required by the generate prompt for any metric the LLM expects to move significantly (catches "won sharpe but blew up drawdown" cases). Scope can be `aggregate`, `regime:<label>`, `fold:<index>`, or `window:<start>:<end>` — restricted scopes evaluate the baseline on the same scope (apples-to-apples).

Accept requires both layers: the mechanical gate AND the LLM's stated claim met. Four-way verdict matrix:

|  Δ beats σ  |  claim met  |  verdict        |
|-------------|-------------|-----------------|
|    ✓        |     ✓       |  survives to verdict-critique |
|    ✓        |     ✗       |  falsified — outcome real, prediction wrong |
|    ✗        |     ✓       |  noise — claim was too weak |
|    ✗        |     ✗       |  reject both reasons |

The `falsified` vs `noise` distinction is a learning signal for next-iteration generate.

### Decision: Per-strategy storage layout under `ledger/strategies/<strategy_name>/`

Each strategy accumulates its own history of decisions, source blobs, and LLM responses, isolated from siblings. Strategy identity is the strategy crate name (already stable across the repo).

```
ledger/
  strategies/
    vxx_volatility_range/
      hypothesis_records.parquet
      decision_records.parquet
      baseline/
        files_manifest.json
        best.json
      sources/
        <files_set_hash>/{Cargo.toml, src/lib.rs, ...}
      responses/
        <decision_id>/{stage1_idea.md, stage2_commitments.md,
                       stage3_files.md, repair_*.md}
```

Source blobs are content-addressed; identical baseline source across candidates deduplicates naturally. Replay reconstructs files from blobs and recompiles via build-pipeline's content-addressed artifact cache.

**Alternative considered**: flat ledger with strategy_id as a column. Rejected because per-strategy queries dominate the audit workload; folder isolation makes them trivial.

### Decision: Simplicity-preferring rank

Two surfaces enforce simplicity:

1. **Prompt-level**: Stage 1 generate prompt instructs the LLM to prefer subtractive ideas when removal does not break the underlying thesis. Rotation rule: at least one subtractive candidate per generation batch when possible.
2. **Rank score**: replace today's binary `_complexity_penalty` with a continuous differential. Two candidates with identical lift and evidence — the simpler one wins.

```
delta_params      = added_count - removed_count
delta_components  = added_components - removed_components
complexity_delta  = w_p · delta_params + w_c · delta_components

rank = 0.55 · lift
     + 0.25 · evidence
     - 0.15 · max(0, complexity_delta)        # penalize net additions
     + 0.05 · max(0, -complexity_delta)        # reward net removals
```

Weights are starting values; tune after first runs.

### Decision: KB filter informed by prior decisions

`kb_query_node` extends with a deterministic post-retrieval filter that:

- Drops chunks whose `(source, locator)` appears in `kb_cites` of any rejected `prior_decision`.
- Boosts chunks cited by accepted decisions.
- Optionally appends a "rejected directions" hint to the retrieval query string.

The KB client itself remains oblivious. The filter is a Python pass after `client.retrieve`. Replay-safe; no LLM involved.

### Decision: Engine-rt ships PROMPT_API.md as authoritative LLM context; no versioning

The generate prompt embeds engine-rt's full public surface (Strategy trait, Context handle, data types, allowed-crate list, param-declaration convention, file-layout convention, forbidden constructs, minimal exemplar) every call. Source of truth: a hand-maintained `crates/engine-rt/PROMPT_API.md`. KB no longer carries API docs (it carries techniques and concepts).

The doc is hand-maintained, not auto-generated, because `cargo doc` JSON output is verbose, prompt-unfriendly, and lacks the targeted "how to write a strategy" framing.

No `runner_version` is recorded on hypothesis records. Engine-rt updates that break replay are an accepted limitation; the project has chosen not to support multi-version replay.

### Decision: Allowed-crate whitelist is strict; dependency suggestions are not collected

LLM prompts emphasize that candidates may use only crates from the existing build-pipeline whitelist. Cargo.toml with unlisted crates hard-rejects via the build-pipeline's allowed-crate check; the rejection feeds the repair loop with "remove the unlisted dep" guidance. No "Dependency Suggestions" section in the emit contract — even an advisory list invites the LLM to lean on it as an escape hatch.

### Decision: Hand-maintained PROMPT_API.md; no auto-extraction

See above. Tradeoff: maintenance burden on engine-rt evolution. Acceptable at current crate size (~hundreds of LoC of public surface). When the surface grows large enough to make this painful, revisit.

## Risks / Trade-offs

[Cost ceiling] → Per-candidate mini-optimize is the dominant cost. K=4 candidates × 64 trials × 5 folds × 3 iterations = ~3,840 backtests per hypothesize run. **Mitigation**: `--quick` flag (16 trials) for early iteration; `--max-backtests N` hard ceiling rejects iterations that would exceed budget; default values are conservative and tunable per spec.

[LLM rambling] → Free-form rationale can grow unbounded. **Mitigation**: rationale field capped at 500 chars on parse; full raw response preserved in `stage1_idea.md` blob.

[Replay break on engine-rt update] → No runner-version pinning means a candidate built against an older PROMPT_API surface may fail to recompile after engine-rt evolves. **Mitigation**: documented as accepted limitation; archived hypothesize runs may not replay byte-identically across major engine-rt changes. Future change can introduce versioning if the cost becomes material.

[LLM emits unlisted dep anyway] → Even with prompt emphasis, models occasionally add a crate outside the whitelist. **Mitigation**: build-pipeline's existing allowed-crate check rejects deterministically; repair loop synthesizes a "remove this dep" message; K_repair=2 absorbs the typical retry.

[Mechanical-gate false negative] → A genuinely strong candidate with high fold variance gets rejected as noise. **Mitigation**: borderline flag carried to verdict-critique surfaces the case to operator review via persisted rejection rationale; the candidate can re-emerge in a later iteration with cleaner evidence. No override path is offered (see decision).

[Multi-stage cost on small candidates] → Three LLM calls feel heavy for a tiny tweak. **Mitigation**: cost is small compared to mini-optimize; the multi-stage benefit is per-call quality, not raw call count.

[State-graph wiring duplicates existing pure-function calls] → LangGraph wrappers around existing node functions can feel boilerplate. **Mitigation**: pure functions remain the canonical implementation; the StateGraph is a thin orchestration layer that lets the workflow be inspected and resumed. Tests exercise pure functions directly.

[Per-strategy ledger migration] → Existing flat ledger entries (if any) cannot read under the new layout without migration. **Mitigation**: the partial implementation has not been used at scale; one-time discard documented in the change. If real ledger content exists at archive time, a migration script lands as a task.

[KB filter overfits to past rejections] → Suppressing every chunk cited by any rejected decision could starve generate of context. **Mitigation**: filter only suppresses chunks tied to *rejected* decisions where the rejection rationale relates to the chunk's claim; accepted-decision boost provides counterweight; final fallback retrieves an unfiltered top-k if filter empties the result set.

[Cheap-critique kills good ideas early] → Idea-level rejection might be too aggressive at low temperatures. **Mitigation**: cheap-critique rationale persists on rejected DecisionRecord; intra-run history surfaces it to subsequent generates so a borderline idea can be re-emitted with refined framing.

## Migration Plan

1. **Pre-rewrite preserve**: snapshot any existing ledger entries (if non-empty). The current partial implementation is unlikely to have produced production-meaningful records, but the snapshot lets the team verify nothing meaningful is lost.
2. **Phase A foundation**: PROMPT_API.md authored; param-declaration convention chosen; build-pipeline introspection surface exposed; per-strategy ledger layout and source-blob storage implemented; markdown parser + per-stage YAML schemas built; KB filter added.
3. **Phase B candidate generation**: multi-stage prompt builders; real ReasoningClient implementations; cheap-critique node; repair loop with per-stage K_repair and error synthesizers.
4. **Phase C candidate evaluation**: tester `attempt_with_optimize` surface; mechanical gate; verdict-critique node; rank score update.
5. **Phase D orchestration**: LangGraph StateGraph assembly (wraps existing pure-fn nodes); orchestrator entry; CLI `hypothesize` subcommand.
6. **Phase E operability**: replay machinery; smoke fixture rewrite; documentation (how-to, reference, ADRs).
7. **No rollback path needed**. The capability has no production consumers today; the rewrite is a forward-only enhancement.

## Open Questions

- **Exact `StrategyParams` declaration convention**. Macro vs. trait-object vs. associated-const-table. The choice affects build-pipeline introspection ergonomics. Resolve during Phase A foundation work; lock the convention in `PROMPT_API.md` once chosen.
- **`σ_combined` / `fold_cv` thresholds for unfamiliar metrics**. The variance-aware gate's `k = 1.0` and `cv < 0.5` defaults are placeholders; first real run on the VXX strategy will inform tuning. Per-objective overrides may be needed (e.g., drawdown variance is naturally lower than sharpe variance).
- **Intra-run history payload size**. As iterations accumulate, the "this hypothesize run's prior candidates" context grows. Decide whether to summarize older candidates after iteration 2 or to truncate by rejection-reason category. Defer until measured.
- **Where the `optimize` follow-up sits relative to hypothesize**. Accepted candidates land with `param_intent.bounds` that downstream `optimize` could exploit, but the wiring (auto-launch optimize on accept vs. manual operator step) is deferred to a follow-up change.
- **CLI defaults for `--k-candidates` and `--iteration-budget`**. The current `HypothesisLoopConfig` defaults (target_candidates=3, iteration_budget=4) were sized for a stub-driven flow. Real flows may warrant `k=4, iterations=3` or similar. Tune after first end-to-end run.
