## Why

The current `docs/how-to/cli-cookbook.md` is a 609-line prose page organized by subsystem (Datasets, Strategies, Author, Hypothesis, Optimize). It is hard to navigate end-to-end, it omits three real CLI commands (`version`, `hypothesis replay`, `hypothesis diff`), its trailing quick-reference table is stale (lists `ingest` and `hypothesize` as "CLI stubs" when both are now real commands), and its structure does not mirror how an operator actually uses the system. An operator authoring a new strategy reaches for `author`, then `run`, then `optimize`, then `hypothesize`, then back to `optimize` — but the cookbook forces them to hop between four sections to follow that path.

Operators need a guided walkthrough that traces the natural usage arc (set up → explore → fetch data → author → smoke → optimize → hypothesize → iterate → reproduce) and points to the existing how-to / tutorial / reference pages for depth. Commands that appear in multiple stages (e.g. `optimize inspect` during stage 5 and during iteration in stage 7) should appear in each relevant stage, because context — not deduplication — is what helps the operator.

## What Changes

- **NEW** top-level page `docs/guided-cli-walkthrough.md` organized into nine usage stages:
  0. Setup (one-time install + workspace build)
  1. Explore the command surface
  2. Acquire data (fetch, cache modes, materialize)
  3. Author a strategy (interactive dialog, edit-mode, repair budget)
  4. One-shot backtest (`run`, async handle, params tweaks)
  5. Optimize (root + `inspect`, `replay`, `reselect`, `compare`; method overrides; PBO)
  6. Hypothesize improvements + KB (baseline modes, dry-run, `hypothesis replay`, `hypothesis diff`)
  7. Iterate (next cycle: re-optimize, hypothesize from new baseline, audit decisions)
  8. Reproduce and debug (ledger replay, offline cache, ledger inspection)
- **NEW** reusable cross-cutting snippet `docs/_includes/cli-cross-cutting.md` (mkdocs-material `--8<--` syntax) covering progress modes, required env vars, and root flags. Each stage embeds this snippet, so the cross-cutting reminder appears consistently without drift.
- Each stage is written as prose with command snippets in fenced blocks; the format is `<paragraph framing the task> + <fenced command> + <one or two sentences explaining what it does>` rather than a wide table — commands are too long to read in a table column.
- Each stage carries a "See also" subsection linking to the existing how-to / tutorial / reference pages that go deeper. The walkthrough deliberately stays surface-level; depth lives in linked pages.
- Stage 0 (Setup) includes the adjacent tooling commands needed to make the CLI work end-to-end: `cargo build -p engine-worker`, `cargo build -p vxx-strategy`, `maturin develop -m crates/py-bindings/Cargo.toml`, and the required env vars. It does NOT cover lint / test commands; those belong to a contributor workflow, not an operator workflow. The page calls out this scope choice explicitly.
- **REMOVE** `docs/how-to/cli-cookbook.md`. The new walkthrough fully supersedes it. Every existing link to `cli-cookbook.md` in `docs/` (for-quants index, how-to index, three tutorials) is repointed at `docs/guided-cli-walkthrough.md` (or a stage anchor within it).
- Update `mkdocs.yml` nav: add the walkthrough as a top-level nav entry positioned before the four Diátaxis quadrants (so it functions as the front-door guided manual); remove the cookbook entry from the how-to nav.
- Update `docs/index.md` to point new readers at the walkthrough as the primary entry into the CLI surface.

## Capabilities

### New Capabilities

(none — the walkthrough is documentation under the existing `documentation` capability)

### Modified Capabilities

- `documentation`: the Diátaxis quadrant requirement currently exempts `index.md`, audience reading paths, decisions, and the bibliography from quadrant placement. The walkthrough adds one more top-level exempt page (`docs/guided-cli-walkthrough.md`). The "CLI cookbook covers the `author` command surface" requirement is replaced by a requirement that the guided walkthrough's Stage 3 covers the same author CLI surface (and more). The "Existing documentation is relocated into the Diátaxis layout" requirement's bullet for `cli-cookbook.md` is dropped (the file is being removed, not relocated).

## Impact

- `docs/guided-cli-walkthrough.md` (new, top-level)
- `docs/_includes/cli-cross-cutting.md` (new, reusable snippet)
- `docs/how-to/cli-cookbook.md` (removed)
- `mkdocs.yml` (nav reshuffle; enable `pymdownx.snippets` if not already on)
- `docs/index.md`, `docs/for-quants/index.md`, `docs/how-to/index.md` (repoint links)
- `docs/tutorials/author-a-strategy.md`, `docs/tutorials/first-backtest.md`, `docs/tutorials/running-an-optimization.md` (repoint links)
- `openspec/specs/documentation/spec.md` (delta applied on archive)
- No code changes; no engine, runtime, or build-pipeline impact.
- Link integrity is enforced by the existing `mkdocs build --strict` gate (already part of `make lint`).
