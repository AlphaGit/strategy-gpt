# 0016 — `engine-rt/PROMPT_API.md` is the authoritative LLM context

## Context

The hypothesis loop emits Rust strategy crates by asking a reasoning model to
fill in the `Strategy` trait against the `engine-rt` public surface. Earlier
sketches embedded API documentation in the knowledge base (KB) alongside the
trading concepts the loop is supposed to reason about. That coupling has two
costs:

- The KB now carries two unrelated payloads — domain ideas (regimes, hedge
  ratios, indicators) and a moving target (the strategy ABI) — which corrupts
  retrieval relevance scores.
- The KB ingestion pipeline becomes the bottleneck for ABI changes; every
  edit to `engine-rt::Context` would need a corresponding KB ingest.

`cargo doc --output-format json` exists but is too verbose for a prompt
context window, lacks the "how to write a strategy" framing the model needs,
and includes private items the linter would reject anyway.

## Decision

`crates/engine-rt/PROMPT_API.md` is a hand-maintained, prompt-shaped document
that is the **single source of truth** for the Strategy trait, Context handle,
data types, allowed-crate list, file-layout convention, param-declaration
convention (see [0017](0017-per-strategy-storage-layout.md)), forbidden
constructs, and a minimal exemplar.

Every reasoning call in the hypothesis loop that emits strategy code embeds
the verbatim contents of this file as locked context. The KB no longer carries
API documentation — it carries techniques and concepts only.

Maintenance is co-located with `engine-rt` source: any change to the public
surface lands in the same commit as the document update.

## Consequences

- The KB stays focused on domain knowledge; retrieval relevance scores stop
  being polluted by ABI documentation chunks.
- ABI evolution is a one-file edit alongside the source change — the loop
  picks it up on the next prompt build with no re-ingestion step.
- The maintenance burden is on humans, not tools. Acceptable at the current
  crate size (low hundreds of LoC of public surface); revisit if the surface
  grows beyond what a single document can stay current with.
- Prompt size grows by the size of `PROMPT_API.md`. For typical reasoning
  models this is a few thousand tokens — small relative to the candidate
  rationale and code that follow.

## Alternatives Considered

- **Auto-generate from `cargo doc` JSON.** Rejected — output is too verbose,
  lacks framing, and includes items the strategy linter would reject. The
  signal-to-noise ratio for the model is much worse than a hand-curated
  document.
- **Embed the API in the KB.** Rejected — couples a moving target with the
  domain corpus and corrupts retrieval. Forces every ABI edit through the
  ingest pipeline.
- **Generate prompt context from `engine-rt` source via a custom
  extractor.** Rejected — the maintenance burden of the extractor itself
  exceeds the burden of keeping a document current at current crate size.

## Status

accepted
