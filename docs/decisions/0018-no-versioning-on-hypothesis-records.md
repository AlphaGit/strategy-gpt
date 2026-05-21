# 0018 — No `runner_version` field on hypothesis records

## Context

The `engine-rt` crate exposes a `RunnerVersion`
that backtest run records pin (see `RunRecord` in the experiment ledger). Run
records need this because byte-identical replay of a backtest must execute
against the same ABI; the engine worker refuses to load a `cdylib` whose
declared ABI major mismatches the worker's own.

The hypothesis loop produces `HypothesisRecord` rows that describe the
*proposal* — a slate of files, a falsification claim, citations, a baseline
files hash. These records carry the LLM's idea; they don't themselves execute
anything. The runtime-version question only arises when someone tries to
*replay* a recorded hypothesis: rebuild the source set, run mini-optimize,
compare to stored evidence.

An earlier sketch of `HypothesisRecord` carried a `runner_version` field "so
replay knows what to do." On inspection: the field is load-bearing only when
the project commits to multi-version `engine-rt` support. That commitment has
significant cost (vendoring older crates, keeping migration paths green, CI
matrices), and the project does not need it: the strategy artifacts are
disposable, generated on demand, and rebuilt against current `engine-rt` if
replay is required.

## Decision

`HypothesisRecord` does **not** carry a `runner_version` field. The current
`crates/engine-rt/src/version.rs`
remains the live ABI version for the run side, but hypothesis records simply
record what the LLM proposed.

Replay of an archived hypothesis is best-effort against the current
`engine-rt`. When the surface evolves in an incompatible way, archived
records may fail to recompile. That is an accepted limitation; the source
blobs are preserved in the per-strategy ledger ([0017](0017-per-strategy-storage-layout.md))
so an operator who needs exact reproduction can check out the matching
`engine-rt` commit from git history alongside the source.

## Consequences

- Hypothesis records stay narrow: rationale, claim, files, citations. No
  ABI metadata bleeds into idea-level records.
- The project does **not** owe multi-version `engine-rt` replay. The
  `RUNNER_VERSION` constant continues to bump on breaking changes; old run
  records flag mismatch at load time, but old hypothesis records simply
  rebuild against current.
- Source blobs are still byte-preserved per-decision (see 0017), so an
  operator who wants strict reproduction can recover the original
  `engine-rt` revision from git and rebuild — the project supports that
  workflow off-band, just not as an automated replay path.
- A future change can introduce versioning if the cost becomes material
  (e.g. the loop produces records meant to survive years). The field can be
  added later without retroactively populating archived records.

## Alternatives Considered

- **Carry `runner_version` on `HypothesisRecord` and refuse replay on
  mismatch.** Rejected — drags the project into multi-version ABI support
  that is not aligned with the research-platform goal. Easier to rebuild
  the source set against current.
- **Carry `runner_version` only on the `DecisionRecord.evidence` payload.**
  Rejected for the same reason — evidence is opaque JSON today; embedding
  ABI metadata there invites readers to depend on its presence and recreates
  the same coupling.
- **Pin `engine-rt` major in the hypothesis record and refuse to update
  `engine-rt` while archived hypotheses exist.** Rejected — freezes the
  whole crate for the lifetime of any archived record.

## Status

accepted
