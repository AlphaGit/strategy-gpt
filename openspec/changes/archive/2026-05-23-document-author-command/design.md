## Context

`strategy-gpt author` is the documented root primitive for the research loop, but the tutorial layer still teaches hand-authoring as the default path. `docs/tutorials/authoring-a-strategy.md` walks through copying `example-strategy/`, renaming the crate, hand-implementing `Strategy`, and running `cargo build` directly. That guide is technically accurate and useful for engineers extending the trait surface, but it is the wrong first read for an operator whose entry point is `strategy-gpt author "trend-follow SPY"`.

The `docs/how-to/author-a-strategy.md` how-to was added in `add-author-command`, but:

- It is not surfaced from `docs/tutorials/index.md` (only mentioned as a tip inside the old tutorial).
- It is not linked from the CLI cookbook even though that page is the canonical "how do I drive this command" recipe collection.
- There is no tutorial-shaped walkthrough that pairs a frozen NL seed with expected dialog output and on-disk artifacts — the format the rest of the tutorials follow.

The fix is a documentation layout change, not a code change.

## Goals / Non-Goals

**Goals:**

- Make `strategy-gpt author` the discoverable default for creating a new strategy.
- Provide a tutorial-shaped walkthrough that follows the existing skeleton (`Learning goal` / `Prerequisites` / `Walkthrough` / `What you just did` / `What next`) and is reproducible offline.
- Preserve the hand-authored content as engineer-targeted reference under Explanation; do not lose the trait-implementation walkthrough.
- Surface the author surface from CLI cookbook so it shows up in the same recipe collection as `fetch`, `run`, `hypothesize`, and `optimize`.
- Encode the new structure as `documentation` capability requirements so CI catches future drift.

**Non-Goals:**

- No new code paths. The author command, its prompts, and its tests are already in place.
- No live-LLM tutorial. Live-LLM author runs are non-deterministic; the tutorial uses a frozen prompt + a description of the expected dialog beats, not a transcript that needs to match byte-for-byte.
- No new ADRs. The relocation is a Diátaxis-quadrant tidy-up, not a load-bearing architectural decision.
- No reference-quadrant entry for `AuthorIntent` / `smoke.toml` schemas — those land in a follow-up if/when the schemas are externally consumed.

## Decisions

### 1. Tutorial covers a *frozen* dialog walkthrough, not a live LLM session.

The tutorial shows: (a) the exact `strategy-gpt author "..."` invocation, (b) the LLM's likely first 2-3 clarifying questions and example operator answers, (c) the expected on-disk artifacts (`crates/<name>-strategy/{Cargo.toml,src/lib.rs,smoke.toml,intent.toml}`), and (d) how to read back `intent.toml` via `load_intent_toml`. The dialog transcript is shown as illustrative, not as expected-output to match. The tutorial explicitly states "the actual dialog will vary; what matters is the *shape* of the on-disk artifacts."

Alternative considered: ship a fully reproducible tutorial that uses the dependency-injection hook (`_author_reasoning_client_factory`) to plug in a stubbed dialog. Rejected because (a) the hook is internal API not meant for users, (b) the install surface (env-var setup, hook injection) would balloon the tutorial, and (c) the existing first-backtest tutorial accepts similar variability in `metrics` output without trouble.

### 2. Hand-authored guide relocates to `docs/explanation/hand-authoring-a-strategy.md`.

Explanation quadrant fits the reframed purpose: "understand the trait surface the LLM is targeting" rather than "follow these steps to ship a strategy." The page keeps its walkthrough body verbatim (engineers extending the engine still need it) but the intro is rewritten to position it as deep reference rather than the recommended workflow.

Alternative considered: `docs/reference/strategy-trait.md` (reference quadrant). Rejected because the page is a narrative walkthrough, not an exhaustive schema dump; reference pages here (e.g. `experiment-spec`, `batch-spec`) are field-by-field surfaces.

Alternative considered: drop the page entirely. Rejected — the content is load-bearing for engineers and there is no equivalent prose elsewhere; deleting it loses real institutional knowledge.

### 3. `mkdocs.yml` nav reordering.

Current Tutorials section ordering: `Authoring a strategy` first, then `Your first backtest`, then `Walking the hypothesize loop`, then `Running an optimization`. New ordering: `Your first backtest` first (foundational), `Author a strategy` second (new tutorial, builds on backtest), then `Walking the hypothesize loop`, then `Running an optimization`. The first-backtest-as-foundation ordering matches the existing tutorials/index.md narrative.

### 4. CLI cookbook gets a new top-level `## Author a strategy` section.

Placed after the `## Strategies` block (which currently covers building and running existing strategy crates). The section mirrors the cookbook house style (sub-headed recipes, flag tables, troubleshooting bullets) and links to both the new tutorial and the existing how-to.

### 5. Update CLAUDE.md cross-reference only if it points at the old tutorial.

Audit `CLAUDE.md` for `authoring-a-strategy` link; the previous change added an Author module-role entry without linking the tutorial, so this is likely a no-op verification step, not an edit.

## Risks / Trade-offs

- **`mkdocs build --strict` link checking** — every internal link to `docs/tutorials/authoring-a-strategy.md` MUST be rewritten to the new explanation path. Mitigation: search the docs tree for the old path before declaring the migration done; `make lint` invokes `mkdocs build --strict` which fails CI on any missed reference.
- **The frozen-dialog tutorial can rot** — if the dialog system prompt changes, the example dialog will read as outdated. Mitigation: phrase the dialog illustratively ("the LLM will likely ask something like...") rather than as a transcript, and call out in the page that exact dialog wording is non-load-bearing.
- **Mike-versioned docs** — the relocation is a breaking link change. Mitigation: this is acceptable; the repo policy is to version docs per release branch, and the next release branch will carry the new layout.

## Migration Plan

1. Write the new tutorial.
2. Move (not copy) the hand-authored content to its new explanation home; rewrite the intro.
3. Search the docs tree for every link to `tutorials/authoring-a-strategy.md` and rewrite.
4. Update `mkdocs.yml` nav.
5. Add the CLI cookbook section.
6. Cross-link `tutorials/index.md` and `how-to/index.md`.
7. Run `mkdocs build --strict` locally; fix any broken link.
8. Run `make lint` to confirm CI gate is green.

No data migration. No code migration.

## Open Questions for Implementation

- Whether the new tutorial should use a real symbol (`SPY`, `VXX`) or a synthetic one. Lean toward `SPY` for relatability; defer to the existing first-backtest tutorial style, which uses `VXX`.
- Whether the relocated hand-authoring page warrants an entry in the `tutorials/index.md` "what next" — probably yes as "for engineers extending the trait surface", phrased to not contradict the new default.
