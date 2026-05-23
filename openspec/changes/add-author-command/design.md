## Context

`strategy-gpt` automates the loop `hypothesis → code → backtest → verdict`. Every existing piece of that loop assumes a working strategy exists in `crates/`. The reference example (`vxx-strategy`) was hand-written. There is no production path for a human to *create* a new strategy via the platform — the platform can only iterate on what already exists.

The `author` command fills that gap. It is the root primitive for the expected research flow:

```
author ──▶ backtest (optional) ──▶ hypothesize ──▶ optimize
```

Author's job is narrow: take human intent, produce a Rust strategy crate that compiles and smoke-passes. It does not evaluate metrics, does not pick winners, does not record verdicts. Those concerns belong downstream.

## Goals / Non-Goals

**Goals:**

- A single interactive CLI command that takes optional NL intent, clarifies through LLM dialog, and writes a working strategy crate to `crates/<name>-strategy/`.
- A library-level entry point (`author_strategy()`) so future work can collapse `hypothesis_loop.generate` into a call against the same primitive.
- Edit-mode for iterating on existing crates without a separate command.
- Optional `--verify=batch` flag that runs the full walk-forward verification pipeline already exposed by the engine, against an `experiment.yaml` produced during the dialog.
- Hard-reject of crates outside the build-pipeline whitelist.

**Non-Goals:**

- No ledger record. The on-disk crate (with `intent.toml` and `smoke.toml`) is the durable artifact.
- No falsification, no metric thresholds, no reject taxonomy. Success = compiles + smoke passes.
- No optimization. That is a separate, downstream command.
- No collapse of `hypothesis_loop.generate` into `author_strategy()` in this change. The library seam is designed so a later refactor is mechanical, but the refactor itself is out of scope.
- No data-source recommendation logic. The LLM proposes a smoke fixture based on dialog; the user confirms; `data-gateway` fetches it. If the provider doesn't have the data, smoke fails and the repair loop / dialog handle it.

## Decisions

### 1. Stage model: two stages.

A simpler `dialog → unified emit/build/smoke` decomposition is preferred over a four-stage breakdown (separate param-schema commit, separate code emit, separate verify). Reasons:

- Param schema is small and tightly coupled to the source — emitting them together avoids an extra round trip and an extra repair budget.
- Build and smoke failures share a feedback channel into the same repair loop: cargo diagnostics for build, panic / sanity-trip / zero-trades for smoke.
- The dialog stage is interactive and does not consume a repair budget — its "repair" mechanism is conversational clarification with the user.

Trade-off: schema mismatches and source compile errors compete for the same repair budget. The mitigation is a relatively generous default (`k_repair=2`, so three attempts) plus configurable overrides per stage (`--k-repair-emit`, `--k-repair-build`).

### 2. Write timing: every attempt writes to the final crate path.

The repair loop overwrites `crates/<name>-strategy/{src/lib.rs,Cargo.toml,smoke.toml}` on every emission. `cargo build` runs package-scoped (`cargo build -p <name>-strategy`) so a broken in-progress crate does not break the workspace build for unrelated callers.

Alternative considered: build in a tmp directory, copy to final path on success. Rejected because:

- Users iterating during a long session want to inspect the in-progress crate (e.g. `bat crates/spy-atr-strategy/src/lib.rs`) without flag gymnastics.
- The workspace already tolerates broken members (Rust resolves package scope per `-p`); the cost of "workspace cargo check temporarily fails" is small relative to the inspection benefit.
- Crash-during-session leaves a partial crate on disk by design: the user can re-run `author` against the same name and the dialog will auto-detect edit mode.

### 3. No resume / abort flags.

Failures are handled entirely inside the interactive dialog. When the repair budget for emit-build-smoke is exhausted, the dialog resumes: the LLM summarizes what was attempted, the user clarifies (e.g. "the smoke window is too short, expand to 6 months", "this filter logic is wrong, try threshold instead of percentile"), and the loop retries. There is no `--resume <name>` or `--abort <name>` because everything is in-session. If the user quits mid-session, the partial crate stays on disk; re-running `author` with the same name picks up via edit-mode auto-detection.

### 4. Edit-mode trigger: dialog detects, not CLI flag.

Plain `author` always works. If during dialog the LLM proposes a name that collides with an existing `crates/<name>-strategy/`, the LLM informs the user and asks: "edit `<name>` or pick a different name?" If edit, the LLM loads the existing `intent.toml`, `src/lib.rs`, `Cargo.toml`, and `smoke.toml` into context and frames subsequent emissions as modifications.

No `--edit` or `--new` flag is needed. The dialog is the contract.

### 5. Few-shot exemplars are static and always-on.

Every author prompt contains `crates/vxx-strategy/src/lib.rs` plus `crates/vxx-strategy/Cargo.toml` and the corresponding `example-strategy` files. Token cost is bounded (both crates are small), and the LLM gets both a realistic exemplar (vxx, a real strategy with mechanism) and a minimal one (example-strategy, no-op fixture). Dynamic exemplar selection was considered and rejected as premature — when the crate library grows, this can revisit.

### 6. Hard-reject on non-whitelisted crates.

The LLM receives the build-pipeline crate whitelist as part of its prompt. If it emits a `Cargo.toml` with a crate outside that list, the build pipeline rejects it; the repair feedback string names the offending crate and the rule. The LLM is expected to either drop the dependency or substitute a whitelisted one. There is no "request a new crate" surface — operators who need a new dep add it to the build-pipeline whitelist out-of-band, then re-run author.

### 7. Workspace switches to `members = ["*"]`.

A one-time refactor to `crates/Cargo.toml` so author never has to mutate the workspace manifest. Verified that the existing members (`engine-rt`, `engine`, `data-gateway`, `ledger`, `kb`, `build-pipeline`, `objectives`, `py-bindings`, `example-strategy`, `vxx-strategy`) are all direct children of `crates/` and have no other-workspace cousins, so a glob is safe. Future quarantine subdirs (if needed) can be opted out via `[workspace] exclude = [...]`.

### 8. Library seam: `author_strategy(intent, deps) -> AuthoredStrategy`.

```python
@dataclass(frozen=True)
class AuthorIntent:
    name: str
    description: str
    mechanism_summary: str
    param_schema_sketch: dict[str, ParamSpec]
    smoke_spec: SmokeSpec          # symbols, resolution, range, provider
    experiment_spec: ExperimentSpec | None  # set when --verify=batch
    baseline_crate: Path | None    # set on edit-mode

@dataclass(frozen=True)
class AuthorDeps:
    reasoning_client: ReasoningClient
    build_pipeline: BuildPipeline
    data_gateway: DataGateway
    repair_config_emit: RepairConfig  # default k_repair=2
    repair_config_build: RepairConfig # default k_repair=2

def author_strategy(intent: AuthorIntent, *, deps: AuthorDeps) -> AuthoredStrategy: ...
```

The dialog driver is a separate function (`run_intent_dialog(seed: str | None, ...)`) that returns an `AuthorIntent`. `author_strategy` consumes a fully-formed intent and runs the emit / build / smoke loop. This split lets future callers (hypothesis_loop) bypass the dialog and supply a programmatically-derived intent.

### 9. On-disk artifact set.

Every successful author run produces, in `crates/<name>-strategy/`:

```
src/lib.rs       — LLM-emitted strategy source
Cargo.toml       — manifest (deps within whitelist)
intent.toml      — structured intent record (name, description, mechanism, param sketch, smoke spec, optional experiment spec)
smoke.toml       — fixture data spec (symbols, resolution, range, provider)
experiment.yaml  — full-batch spec (only when --verify=batch was used)
```

The crate directory is the artifact. There is no separate cache entry or ledger row; reproducibility comes from the source-in-repo plus the deterministic build pipeline plus the data-gateway content-addressed cache.

## Risks / Trade-offs

- **LLM cost and latency.** Author is multi-round-trip: dialog (multiple turns) → emit → optional repairs. Budget is bounded by `k_repair` defaults; a single author session is in the same order of magnitude as a hypothesis-loop iteration. Worth flagging in user docs.
- **Smoke is a weaker bar than full verification.** "Smoke passes" means the strategy compiled, ran without panic, and produced trades. It does not mean the strategy is profitable, sensible, or implementable. The proposal explicitly documents this; users who want stronger bars use `--verify=batch` or follow `author` with `hypothesize`.
- **Workspace can be temporarily broken during author sessions.** Mitigation: package-scoped builds during repair. Acceptable cost; alternative (tmp-dir staging) was rejected for the UX reasons in Decision 2.
- **`crates/Cargo.toml` glob may surprise readers expecting an explicit members list.** Documented in CLAUDE.md or the build-pipeline spec.
- **LLM may persistently emit non-whitelisted crates.** The repair budget feedback names the rule, but if the LLM cannot satisfy it within budget, the dialog resumes and asks the user. Operators can override the whitelist out-of-band if a genuinely-needed crate is missing.

## Migration Plan

1. Switch `crates/Cargo.toml` to `members = ["*"]` and verify `cargo check --workspace` is unchanged.
2. Land `author_strategy()` library + tests.
3. Land CLI wrapper with stubbed reasoning client (so tests run without LLM keys).
4. Add docs page (how-to or tutorial — out of scope for this change but referenced for follow-up).

No data migration. No existing CLI surface is renamed or removed.

## Open Questions for Implementation

- Concrete default models for the reasoning client: reuse `hypothesis_loop` defaults or pick a smaller / cheaper model for the dialog turns. Implementation-time decision; documented in tasks.
- Whether the smoke.toml schema should embed a `provider` field or default to the data-gateway's resolver. Implementation-time decision; the spec only requires that the fixture is reproducible.
