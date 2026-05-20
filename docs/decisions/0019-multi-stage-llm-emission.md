# 0019 — Multi-stage LLM emission for hypothesis candidates

## Context

The hypothesis loop emits each candidate strategy logic change as a structured
artifact: an idea + rationale, a falsification claim plus parameter intent,
and a multi-file Rust source map. Earlier sketches asked the reasoning model
to emit all three pieces in a single response — a JSON document containing
multi-line Rust source plus structured fields.

That omnibus shape is fragile. JSON requires escaping every newline and every
quote inside the Rust source; LLMs frequently get nested quoting wrong on
real-world emissions. A single bad escape kills the whole candidate even when
the idea and falsification claim are well-formed and worth keeping.

The omnibus shape also forces the model to commit to all three concerns at
once. A model that gets the idea right but the file map wrong cannot have its
idea preserved across repair attempts — every retry re-opens every commitment,
inviting the model to drift on its own falsification claim mid-repair
("rewrite the criterion to dodge the build error") and erasing the design's
ability to compare apples-to-apples across retries.

## Decision

Candidates are emitted in **three locked-progression stages**, each in
markdown form (not JSON):

1. **Stage 1 — Idea**: `candidate_name`, `rationale` (≤500 chars),
   `expected_lift_confidence`, `expected_side_effects`. Single `# Idea` YAML
   block.
2. **Stage 2 — Commitments**: `# Falsification` and `# ParamIntent` YAML
   blocks, encoding the primary claim, guard constraints, scope, and the
   added / kept / removed parameter sets with bounds.
3. **Stage 3 — Files**: `## <path>` H2 headers followed by fenced code blocks
   for each file; `## DELETE: <path>` headers for deletions.

Each stage runs in a separate reasoning call. The output of stage *N* is
attached verbatim as locked context to stage *N+1* and to any subsequent
repair attempt for stage *N+1* or *N+2*. Stages MUST NOT be re-opened by the
repair loop — once a stage's response parses and validates, that response is
immutable for the remainder of the candidate's lifecycle.

A cheap-critique node runs immediately after stage 1, before stages 2 and 3
are invoked. Idea-level rejections (duplicate of prior reject, contradicts
diagnosis, violates prior accept) fire here, saving stage 2/3 LLM cost on
candidates that would die anyway.

Stage emissions are markdown rather than JSON because LLMs encode code-fenced
blocks reliably; YAML inside fenced blocks handles structured metadata
without escape pain. The strict parser (`python/strategy_gpt/markdown_io.py`)
identifies the offending section on any malformed emission so the repair
loop can synthesize targeted feedback.

## Consequences

**Pros**

- Per-stage emissions are smaller and more focused; per-call quality is
  higher than the omnibus form.
- Cheap-critique on the idea alone saves ~2 LLM calls per candidate when an
  idea is dead on arrival.
- Locked earlier stages preserve apples-to-apples comparison across repair
  attempts.
- Markdown + YAML avoids the nested-escape failure mode that dominates JSON
  emissions of multi-line Rust source.
- Decision records carry one response blob per stage, plus repair attempts,
  giving a full audit trail of how the candidate was built.

**Cons**

- Three reasoning calls instead of one. Token cost rises ~2× per emission;
  this is small compared to the mini-optimize backtest cost that dominates
  the loop's wall time, so the trade-off favors clearer per-stage signal.
- The parser is the load-bearing piece; a parser bug rejects valid emissions.
  Mitigated by `python/tests/test_markdown_io.py` covering round-trip and
  malformation cases.
- An idea that needs to revise its falsification mid-repair cannot do so;
  the candidate must be hard-rejected and re-emitted as a new candidate.
  Considered acceptable: the loop's iteration budget absorbs the cost, and
  ledger persistence preserves the rejected attempt for next-iteration
  learning.

## Alternatives Considered

- **Single omnibus JSON emission.** Rejected on escape fragility (multi-line
  Rust inside JSON forces quoting that LLMs frequently get wrong) and on the
  inability to lock earlier commitments across repair attempts.
- **Two-stage emission (idea+falsification combined, then files).** Rejected
  because cheap-critique is most valuable at the idea level; bundling
  commitments with the idea pays for a stage-2 prompt before any idea-level
  rejection can fire.
- **JSON for metadata + separate markdown for files.** Considered. Rejected:
  the parser surface grows (two grammars instead of one) without buying
  signal — YAML inside fenced markdown blocks handles structured metadata
  cleanly.

## Status

Accepted (2026-05-20).
