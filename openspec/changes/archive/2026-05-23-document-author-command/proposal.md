## Why

The `strategy-gpt author` command is the new root primitive of the research loop, but documentation still treats hand-authored strategies as the default. The `authoring-a-strategy` tutorial walks through copying `example-strategy/` and hand-implementing the `Strategy` trait — a useful engineer-level guide, but no longer the path most operators take. New users land on a tutorial that ignores the command they were sent to learn, and the existing `docs/how-to/author-a-strategy.md` how-to is undiscoverable from the tutorials index and not linked from the CLI cookbook. The documentation surface needs to lead with `author` and demote the hand-authored path to engineer-targeted reference.

## What Changes

- Add a new tutorial `docs/tutorials/author-a-strategy.md` that walks through invoking `strategy-gpt author` end-to-end on a small frozen example (NL seed → dialog answers → emitted crate inspection → smoke pass). The tutorial uses the standard "Learning goal / Prerequisites / Walkthrough / What you just did / What next" skeleton.
- **BREAKING (docs only):** move the existing `docs/tutorials/authoring-a-strategy.md` hand-authored content into a new engineer-targeted location, `docs/explanation/hand-authoring-a-strategy.md`. The page reframes from "this is the default path" to "this is what the LLM is targeting; read this when extending the trait surface, debugging an author emission, or contributing to the engine."
- Update `mkdocs.yml`: replace the `authoring-a-strategy` tutorial entry with the new `author-a-strategy` tutorial; add the relocated hand-authored page under the `Explanation` section; ensure the existing how-to `author-a-strategy.md` is linked from `tutorials/index.md` and `how-to/index.md`.
- Extend `docs/how-to/cli-cookbook.md` with an "Author a strategy" section covering: invoking `author`, `--verify=batch`, repair budget tuning, edit-mode, troubleshooting (budget exhaustion, non-whitelisted dep, smoke-fail-no-trades).
- Update existing cross-references that point to the old tutorial path (`docs/how-to/author-a-strategy.md` "longer hand-authored path" link, `CLAUDE.md` if it references the tutorial).
- Add capability requirements documenting the new tutorial skeleton, the engineer-targeted relocation, and the CLI cookbook coverage so future doc drift fails CI.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `documentation`: add requirements for the author-command tutorial, the relocation of the hand-authored guide into the Explanation section, and the cookbook coverage of the author surface. Touches existing requirements `Documentation follows the Diátaxis quadrant structure`, `The repository ships at least four initial tutorials`, and `Tutorial walkthroughs use the bundled reference strategy or stub data`.

## Impact

- New file: `docs/tutorials/author-a-strategy.md`.
- Relocated file: `docs/tutorials/authoring-a-strategy.md` → `docs/explanation/hand-authoring-a-strategy.md` (content preserved verbatim with a short reframing intro).
- Modified files: `mkdocs.yml` (nav restructure), `docs/how-to/cli-cookbook.md` (new Author section), `docs/how-to/author-a-strategy.md` (update the "longer hand-authored path" cross-reference), `docs/tutorials/index.md` (point at new tutorial), `docs/how-to/index.md` (surface author how-to), `CLAUDE.md` (if it links the tutorial).
- No code changes. No API changes. CI gate `mkdocs build --strict` must stay green; the relocation requires updating every link that points to the old tutorial path.
- The author tutorial uses a stubbed / scripted dialog (frozen NL seed + canned answers) so the walkthrough is reproducible offline. Live-LLM behavior is documented in the how-to, not the tutorial.
