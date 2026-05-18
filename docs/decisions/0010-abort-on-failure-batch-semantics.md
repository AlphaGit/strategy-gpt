# 0010 — Abort-on-failure batch semantics

## Context

A `BatchSpec` packs many runs against a single strategy + dataset. A run can fail for many reasons: panic, OOM, timeout, sanity-bound violation, invalid `RunSpec`. The orchestrator must decide what happens to sibling runs when one fails. The research loop wants fast feedback on a broken strategy and clear attribution of *which* run is responsible.

## Decision

Engine batches are **abort-on-failure by default**: any worker reporting a failure causes the coordinator to cancel all in-flight sibling workers and return a `BatchResult` whose status is `failed` with the first error attributed. Operators who need different semantics (continue on per-run failure, collect all errors) can override per-batch, but the default and the recommended behavior is abort.

## Consequences

- Fast loop iteration: a broken strategy stops the batch immediately and surfaces the offending run.
- Saves wall-clock when one bad parameter set would otherwise drag the batch into time-cap territory.
- Partial results from cancelled siblings are not returned; replays use seed + spec to reproduce the same point exactly.
- Optimizer runs that *want* to see every candidate's outcome (e.g. to update a TPE surrogate) must opt out of abort semantics for that batch; the optimizer driver does this where relevant.

## Alternatives Considered

- **Best-effort batch.** Continue all runs and collect successes. Considered but the research loop's primary need is fast attribution, not coverage.
- **Per-run isolation only.** Equivalent to running N separate batches; loses batched compile-once amortization.

## Status

accepted
