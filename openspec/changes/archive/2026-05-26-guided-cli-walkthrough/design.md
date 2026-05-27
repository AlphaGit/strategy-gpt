## Context

The CLI surface has grown to roughly 15 user-facing subcommands across the root namespace and the `hypothesis` / `optimize` subgroups. The existing `docs/how-to/cli-cookbook.md` page predates several of those commands and is organized by subsystem (Datasets, Strategies, Author, Hypothesis, Optimize). Operators reading it have to hop between sections to follow a natural research arc (author → smoke → optimize → hypothesize → re-optimize). Three commands (`version`, `hypothesis replay`, `hypothesis diff`) are missing entirely, and the trailing quick-reference table mislabels `hypothesize` and `ingest` as "CLI stubs". The documentation spec (`openspec/specs/documentation/spec.md`) currently locks the cookbook's existence and one author-related requirement to that file.

The Diátaxis structure (tutorials / how-to / reference / explanation) is the right home for *deep dives* but is a poor fit for a guided walkthrough that deliberately spans every quadrant. The existing spec already exempts `index.md`, audience reading paths, decisions, and the bibliography from quadrant placement; the walkthrough fits naturally as one more top-level exempt page.

## Goals / Non-Goals

**Goals:**
- Replace the cookbook with a single guided-walkthrough page that mirrors the operator's actual usage arc, from setup through iteration.
- Cover every operator-facing subcommand at least once, in the stage where it is most useful. Commands used in multiple stages (e.g. `optimize inspect`) appear in each relevant stage; deduplication is not a goal.
- Keep explanations at framing-and-purpose depth; defer full flag references and conceptual depth to the existing how-to / tutorial / reference pages via "See also" links.
- Maintain a single source of truth for the cross-cutting reminders (progress modes, env vars, root paths) so the text cannot drift across stages.
- Make the page the front door of the docs site — surfaced from `docs/index.md` and the top of the mkdocs nav.

**Non-Goals:**
- The walkthrough is not a tutorial. It does not run a literal end-to-end example from raw input to final accepted hypothesis; the existing tutorials (`tutorials/first-backtest.md`, `tutorials/author-a-strategy.md`, `tutorials/running-an-optimization.md`) already do that.
- The walkthrough does not cover developer / contributor workflows (`make lint`, `make test`, `pre-commit`, `cargo check --workspace`). Stage 0 explains the scope choice in one sentence and links to the contributor docs as the place to look for those.
- The walkthrough does not document Python APIs (`strategy_gpt.gateway.Gateway`, `HypothesizeDeps`, etc.). The "Materialize bars to JSON" recipe is the one exception where a Python snippet is unavoidable because no CLI primitive exists; that snippet is borrowed verbatim from the current cookbook.
- No CLI command changes. No code changes. Behavior identical; only documentation moves.

## Decisions

### Page shape: prose + fenced snippets, not tables

Commands like `strategy-gpt hypothesize <name> --baseline-from <opt-id> --objective sortino --quiet` exceed a comfortable table-cell width. Each recipe is written as one short framing paragraph, then a fenced bash block, then one or two sentences explaining the *why* of the command (what state it produces, when to reach for it). The pattern is consistent across stages and degrades gracefully on narrow screens.

The first cookbook approach (a sortable table per stage) was rejected because commands wrap mid-cell and the operator loses the eye line between command and rationale.

### Stage anchors are stable and links target them

Each H2 stage section gets an explicit stable id (`{#stage-3-author}` via mkdocs-material attribute lists) so other pages can link directly to a stage rather than the page top. This is the migration affordance: every page that today links to `cli-cookbook.md#parameter-optimization` retargets to `guided-cli-walkthrough.md#stage-5-optimize`.

### Cross-cutting reminder lives in `docs/_includes/cli-cross-cutting.md`

Embedded into each stage via the mkdocs-material `--8<--` snippet directive (`pymdownx.snippets` extension). The snippet covers, in three short paragraphs:

1. **Progress modes** — `--progress {auto,plain,json,off}`, default `auto`, pipe-friendly.
2. **Env vars** — `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` (whichever the chosen model needs), `RUSTC_WRAPPER=sccache` recommended for fast Rust rebuilds.
3. **Roots** — `--cache-root` (gateway cache), `--ledger-root` (run history), `--gateway-root` (alias used by some commands), `--work-root` (build scratch).

Repeating these inline per stage was considered but creates drift risk. A single include keeps the text canonical without forcing the operator to chase a separate appendix page.

The snippet file lives under `docs/_includes/` so mkdocs picks it up via the `snippets` extension `base_path` setting; the underscore prefix signals "not a published page" by convention.

### Stage 0 covers operator-facing adjacent tooling, not contributor tooling

Stage 0 includes the commands an operator *must* run before any CLI subcommand will succeed: `cargo build -p engine-worker`, `cargo build -p vxx-strategy` (reference strategy for trying it out), `maturin develop -m crates/py-bindings/Cargo.toml`, venv activation, and the env var setup. It explicitly does **not** include `make lint`, `make test`, `pre-commit install`, or `cargo check --workspace`, because those are contributor / CI concerns; an operator running prompts and reading results never needs them. The page calls out this scope decision in one sentence with a pointer to `CONTRIBUTING.md` (existing) / `CLAUDE.md` for contributor tooling.

### Removal, not deprecation, of the old cookbook

The cookbook file is deleted in the same change. There is no transitional period: every internal inbound link (six occurrences across `docs/`) is repointed in the same PR, and the `mkdocs build --strict` gate catches any link the change missed. External bookmarks (search-engine cached URLs) will 404; the project's docs versioning via `mike` keeps the cookbook reachable in any prior published version (`/v0.3/how-to/cli-cookbook.md`), so a bookmark to a versioned URL still works. The dev slot drops the page on the next deploy.

### Nav placement

`mkdocs.yml` nav surfaces the walkthrough as a top-level entry before the four Diátaxis groups:

```
- Home: index.md
- Guided CLI walkthrough: guided-cli-walkthrough.md
- Tutorials: ...
- How-to: ...
- Reference: ...
- Explanation: ...
- Decisions: ...
- For Quants: ...
- For Engineers: ...
```

This positions the page as the front-door manual without disrupting the Diátaxis groupings underneath.

### Spec evolution

The `documentation` capability is modified in two specific ways:

1. The Diátaxis-quadrant scenario's exemption list (`index.md`, audience reading paths, decisions, bibliography) gains one more entry: the guided walkthrough.
2. The existing "CLI cookbook covers the `author` command surface" requirement is removed. The replacement requirement scopes the same author-CLI coverage (and more) to Stage 3 of the walkthrough.
3. The "Existing documentation is relocated into the Diátaxis layout" requirement has its `cli-cookbook.md` bullet dropped: the file no longer exists.

## Risks / Trade-offs

- **External bookmarks break** → mitigated by mike-versioned docs (prior versions still host the old page). New users are unaffected; long-tail traffic is small for a research tool's docs.
- **Drift between stages and depth pages** → mitigated by the cross-cutting include + explicit "See also" subsections per stage. The walkthrough is intentionally shallow; depth pages remain canonical for flag tables and methodology.
- **Stage anchors become a maintenance surface** → mitigated by keeping the stage count and naming stable (numbered 0-8) and treating renames as breaking changes for inbound link rewrites. Stage IDs are documented in the spec's scenarios so reviewers catch accidental renames.
- **Stage 4 (one-shot backtest) and Stage 5 (optimize) overlap on `experiment-spec`** → mitigated by linking both stages at `docs/reference/experiment-spec.md` rather than re-explaining the spec twice. Each stage frames the spec from its own angle (single-run vs sweep).
- **Cross-cutting include adds an mkdocs extension dependency** → `pymdownx.snippets` ships with `pymdown-extensions` which is already in `requirements-docs.txt` transitively; only the `mkdocs.yml` markdown_extensions list needs an entry. No new pip dependency.

## Migration Plan

1. Create `docs/_includes/cli-cross-cutting.md` and wire `pymdownx.snippets` into `mkdocs.yml`.
2. Create `docs/guided-cli-walkthrough.md` with all nine stages and the cross-cutting include in each.
3. Update `mkdocs.yml` nav.
4. Update `docs/index.md` to point at the walkthrough.
5. Repoint inbound links in `docs/for-quants/index.md`, `docs/how-to/index.md`, and the three tutorial pages.
6. Delete `docs/how-to/cli-cookbook.md`.
7. Run `mkdocs build --strict` (also covered by `make lint`) to confirm zero broken links.

Rollback strategy: revert the PR. The old cookbook returns; no data state involved.

## Open Questions

- Should the walkthrough live at `docs/guided-cli-walkthrough.md` (chosen) or under a new top-level group `docs/guide/` to leave room for sibling guides later? Current choice is the flat single-file path; revisit only if a second top-level guide appears.
- Should Stage 8 (Reproduce / debug) include a recipe for inspecting `ledger/optimizations.sqlite` directly via `sqlite3`? Leaning yes (it is the most direct way to audit optimizer history), but the spec currently treats the SQLite schema as internal. Resolved by mentioning it as a "for the curious" footnote rather than a recipe, keeping the schema unspecified.
