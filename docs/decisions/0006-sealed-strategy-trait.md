# 0006 — Sealed `Strategy` trait, no backwards compatibility

## Context

The `engine_rt::Strategy` trait is the ABI surface that every strategy artifact targets. The runner exposes a `RunnerVersion`; strategies are compiled against it and loaded by the engine worker via `libloading`. Pre-1.0, the trait is evolving — new lifecycle hooks, capability methods, and metadata fields are added as the research loop matures.

## Decision

The `Strategy` trait is **sealed**: only types in this workspace can implement it (the trait's super-trait carries a hidden marker, and `strategy_entry!` is the only ABI-registration surface). `RunnerVersion` is a single integer carried by the runtime. Major-version bumps to `RunnerVersion` invalidate every existing artifact; there is no multi-version compatibility shim and no migration ABI.

## Consequences

- The team can rename, add, or restructure trait methods freely. There is no need to design every new method to also work for old artifacts.
- LLM-emitted strategies are regenerated from source whenever the runner version bumps. The artifact cache is keyed by `(hash(source), RunnerVersion)`, so old artifacts simply do not match.
- Human-authored strategies under `crates/` must be rebuilt on every runner bump too. Acceptable because the workspace contains only two reference strategies, and the build pipeline rebuilds them as part of CI.
- We cannot ship a "library of pre-compiled strategies" without committing to ABI stability — out of scope today.

## Alternatives Considered

- **Stable ABI.** Forecloses simple trait evolution; the per-method cost of maintaining backward compatibility is large and the payoff (cross-version artifact reuse) is unwanted.
- **Pluggable trait hierarchy with feature flags.** Adds complexity at the ABI boundary that no current research need justifies.

## Status

accepted
