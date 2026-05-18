# Methodology page template

Required skeleton for every page under `docs/explanation/` that documents a quantitative method (PBO, DSR, fold schemes, robust score, walk-forward, etc.). Sections must appear in this order. The Limitations section is **mandatory** and must name at least one condition under which the method fails or degrades.

## Skeleton

```markdown
# <Method name>

## Intuition

<Plain-prose explanation. What problem does this method solve? Why this
approach rather than the obvious naive one? No math here — the reader
should leave with a working mental model.>

## Formalism

### Notation

| Symbol | Meaning |
|--------|--------|
| ...    | ...    |

### Definitions / equations

<LaTeX via `pymdownx.arithmatex`. Use $...$ inline and $$...$$ display.
Number equations only when later sections refer back to them.>

## Worked example (synthetic toy data)

<Self-contained numerical walkthrough using small toy data declared in
the page (inlined arrays, formulas, or short reproducer snippets). Do
NOT use output from `vxx-strategy` or any ledgered run — those drift
when the strategy changes. The example should illustrate the formalism
end to end: inputs → intermediate quantities → output.>

## Assumptions

<Conditions under which the formalism is valid. State each assumption
explicitly. Reviewers should be able to test whether their data
satisfies each one.>

## Limitations

<MANDATORY. At least one failure mode. Include cases like:
- Method degrades when assumption X fails (and how to detect).
- Method is sensitive to knob Y in ways that surprise newcomers.
- Edge cases the implementation does not handle.>

## References

- [<Author Year>](bibliography.md#anchor)
- [<Author Year>](bibliography.md#anchor)

## Related material

- <Pointers to how-to pages and reference pages that touch the same
  topic from operational or knob-surface angles.>
```

## Conventions

- **No inline bibliographic detail.** Cite via [`bibliography.md`](bibliography.md) anchors. New citations require a new bibliography entry in the same commit.
- **Synthetic data only in worked examples.** Reference-strategy output is forbidden — it changes when the strategy changes and the example will silently rot.
- **Plain prose first, math second.** Intuition must stand alone for a reader who skips the Formalism block.
- **Limitations are non-negotiable.** A method page without honest failure modes erodes trust. If you cannot think of a limitation, the method is not yet understood well enough to document.
- **Cross-link.** Methodology pages should link out to operator how-to pages and knob reference pages. Readers navigate by intent.
