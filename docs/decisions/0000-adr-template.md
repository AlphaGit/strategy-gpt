# ADR template

Copy this file when adding a new ADR. File name: `NNNN-kebab-case-slug.md`, where `NNNN` is the next available zero-padded integer in [the decisions index](index.md).

```markdown
# NNNN — <Short decision title>

## Context

<What problem prompted the decision? What constraints, prior decisions,
or external forces shaped it? Two or three paragraphs is plenty.>

## Decision

<What we are doing. State it actively: "We use Rust for the execution
layer." Avoid hedging — the ADR records a position taken.>

## Consequences

<Both wanted and unwanted. What this enables, what it forecloses, what
operational overhead it adds, what risk it accepts.>

## Alternatives Considered

<Each option with one or two sentences on why it was rejected. Include
the obvious naive choice even when it was never seriously on the table —
future readers should not have to guess why it was ruled out.>

## Status

accepted | proposed | superseded by NNNN | deprecated
```

## Conventions

- Status starts at `proposed` and moves to `accepted` when the decision merges.
- When superseding an ADR, do not edit or delete the old file. Update its Status to `superseded by NNNN`; the new ADR references the superseded one.
- Keep ADRs short. The point is to capture *why* — implementation detail belongs in code, specs, or design docs.
