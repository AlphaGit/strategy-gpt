# Spec: documentation

## Purpose

Defines the documentation platform contract for strategy-gpt: Diátaxis quadrant structure, audience reading paths, methodology page skeleton, ADR convention, central bibliography, math + search configuration, release-branch driven versioning via `mike`, and the `mkdocs build --strict` link-check gate.

## Requirements
### Requirement: Docs platform is MkDocs Material with mike, published to GitHub Pages

The repository SHALL provide a documentation site built with MkDocs and the Material theme, versioned with `mike`, and published to GitHub Pages from the `gh-pages` branch. Configuration MUST live at the repository root as `mkdocs.yml`. Python dependencies MUST be pinned in `requirements-docs.txt`.

#### Scenario: Local site build succeeds

- **WHEN** a contributor runs `mkdocs build --strict` from a clean checkout with `requirements-docs.txt` installed
- **THEN** the build exits zero and produces a `site/` directory containing rendered HTML

#### Scenario: Strict mode catches broken internal links

- **WHEN** a `.md` file under `docs/` references a path that does not resolve
- **THEN** `mkdocs build --strict` exits non-zero and names the offending file and link

#### Scenario: Published site is reachable

- **WHEN** changes merge to `main`
- **THEN** CI deploys the rendered site to the `gh-pages` branch via `mike` and GitHub Pages serves it at the project Pages URL

### Requirement: Documentation follows the Diátaxis quadrant structure

`docs/` SHALL be organized into four top-level quadrants: `tutorials/`, `how-to/`, `reference/`, `explanation/`. Each `.md` page MUST live in exactly one quadrant. The navigation defined in `mkdocs.yml` MUST reflect the same structure.

#### Scenario: Page lives in correct quadrant

- **WHEN** a new page is added under `docs/`
- **THEN** the file path matches one of `tutorials/`, `how-to/`, `reference/`, `explanation/` and the `mkdocs.yml` nav entry mirrors the path

#### Scenario: Page outside the four quadrants is rejected

- **WHEN** a `.md` page is added to `docs/` outside the four quadrant directories (excluding `index.md`, audience reading paths, decisions, and the bibliography)
- **THEN** code review rejects the placement or the page is moved into a quadrant before merge

### Requirement: Audience reading paths are curated indexes, not duplicated content

The repository SHALL provide audience reading-path indexes at `docs/for-quants/index.md` and `docs/for-engineers/index.md`. Each MUST consist of an ordered list of links into the four quadrants. Reading-path files MUST NOT contain substantive content that is not also present in a quadrant page.

#### Scenario: Reading path links resolve

- **WHEN** `mkdocs build --strict` runs
- **THEN** every link in `for-quants/index.md` and `for-engineers/index.md` resolves to an existing page

#### Scenario: Reading paths add no duplicated content

- **WHEN** a reviewer compares a reading-path file against the pages it links to
- **THEN** the reading-path file contains only links plus brief framing prose (≤ 2 lines per entry), no copied content

### Requirement: Methodology pages follow a fixed skeleton

Any page under `docs/explanation/` that documents a quantitative method (PBO, DSR, fold schemes, robust score, objective tradeoffs, et al.) SHALL include the following sections in order: Intuition, Formalism, Worked example, Assumptions, Limitations, References. The Limitations section MUST be non-empty and MUST name at least one condition under which the method fails or degrades.

#### Scenario: New methodology page conforms

- **WHEN** a new method explanation page is added
- **THEN** the page contains all six required sections in order, and Limitations names at least one failure mode

#### Scenario: Limitations cannot be omitted

- **WHEN** a methodology page lacks a Limitations section or its Limitations section is empty
- **THEN** code review rejects the page

### Requirement: Methodology worked examples use synthetic toy data

Worked examples in methodology pages SHALL use synthetic toy data designed to illustrate the concept, not output from the bundled reference strategy. Toy data MUST be self-contained in the page (inlined arrays, formulas, or short reproducer snippets) so the example cannot drift with strategy or fixture changes.

#### Scenario: Reference-strategy output is not embedded

- **WHEN** a methodology page presents numerical results
- **THEN** the numbers are derived from synthetic data declared in the page, not from `vxx-strategy` or any other ledgered run

### Requirement: A central bibliography backs all citations

The repository SHALL maintain `docs/explanation/bibliography.md` listing every external work cited by docs. Each entry MUST have a stable anchor (e.g., `## bailey-borwein-lopez-de-prado-zhu-2017`) and include author(s), year, title, and a URL or DOI when available. Pages MUST cite via anchor links (e.g., `[Bailey et al. 2017](../bibliography.md#bailey-borwein-lopez-de-prado-zhu-2017)`), not via repeated inline bibliographic detail.

#### Scenario: Citation resolves to bibliography entry

- **WHEN** a docs page links to a bibliography anchor
- **THEN** `mkdocs build --strict` resolves the anchor and the rendered page produces a working footnote-style link

#### Scenario: New citation requires bibliography entry

- **WHEN** a page introduces a new external reference
- **THEN** an entry is added to `bibliography.md` in the same commit

### Requirement: Math renders via LaTeX

MkDocs SHALL enable the `pymdownx.arithmatex` extension so that LaTeX expressions delimited by `$...$` (inline) and `$$...$$` (display) render as MathJax. GitHub raw view degrading to plain `$...$` is acceptable.

#### Scenario: Inline math renders

- **WHEN** a page contains `$\mathrm{Sharpe} = \mu / \sigma$`
- **THEN** the rendered site displays the formula typeset, not the raw source

### Requirement: Search is provided by the built-in lunr backend

MkDocs Material's built-in client-side search SHALL be enabled. The site MUST NOT depend on Algolia or any other external search service. Per-version search (one index per `mike` slot) MUST work without additional configuration beyond the Material defaults.

#### Scenario: Search returns results for indexed content

- **WHEN** a user enters a term present in the docs corpus into the search box on the rendered site
- **THEN** matching pages are listed in the results without contacting any external service

### Requirement: Architecture Decision Records live in `docs/decisions/`

The repository SHALL maintain ADRs at `docs/decisions/<NNNN>-<slug>.md` where `<NNNN>` is a zero-padded monotonic integer and `<slug>` is kebab-case. Each ADR MUST contain the sections: Context, Decision, Consequences, Alternatives Considered, Status. Status values are `proposed`, `accepted`, `superseded by NNNN`, or `deprecated`.

#### Scenario: New ADR matches template

- **WHEN** an ADR is added
- **THEN** the filename matches `^[0-9]{4}-[a-z0-9-]+\.md$` and the file contains the five required sections

#### Scenario: Superseded ADR remains in tree

- **WHEN** an ADR is superseded by a later decision
- **THEN** the older file remains in `docs/decisions/`, its Status is updated to `superseded by <NNNN>`, and the new ADR's Status references the superseded one

### Requirement: Load-bearing existing decisions are backfilled as ADRs

Before this change archives, ADRs SHALL be authored for each of the following decisions already operating in the codebase: Rust for execution layer, Python for orchestration, PyO3 in-process boundary for trusted crates, subprocess + Arrow IPC for engine workers, no worker sandboxing, sealed `Strategy` trait with no backwards compatibility, SQLite + parquet ledger, hybrid graph+vector knowledge base over SQLite, year-segmented content-addressed cache, abort-on-failure batch semantics, PBO threshold default of 0.5, mean as the only OOS aggregator, Rust 1.82.0 toolchain pin, lint stance (Rust tool-defaults + Python strict ruleset), and the docs-platform decision itself.

#### Scenario: Each seed decision has an ADR

- **WHEN** this change is archived
- **THEN** `docs/decisions/` contains at least one ADR per item in the backfill list, each with Status `accepted`

### Requirement: Documentation is versioned by release branches via mike

`mike` SHALL drive version slots. Pushes to `main` MUST update the `dev` slot. Pushes to any branch matching `release/v*` MUST update a version slot named after the minor line (`vX.Y`). The `latest` alias MUST point at the highest minor `release/vX.Y` branch present. Git tags SHALL be human markers only and MUST NOT trigger doc deploys.

#### Scenario: Push to main updates dev slot

- **WHEN** a commit lands on `main`
- **THEN** CI runs `mike deploy --update-aliases dev` and the `dev` version on the site reflects the new content

#### Scenario: Push to release branch updates minor slot

- **WHEN** a commit lands on `release/v0.3`
- **THEN** CI runs `mike deploy --update-aliases v0.3` and the `v0.3` version on the site reflects the new content

#### Scenario: Higher minor cut updates latest alias

- **WHEN** a new `release/v0.4` branch is created and receives its first deploy
- **THEN** CI moves the `latest` alias to `v0.4`

#### Scenario: Tag push does not deploy

- **WHEN** a tag matching `v*` is pushed
- **THEN** no `mike` deploy job runs in response to that tag

### Requirement: Existing documentation is relocated into the Diátaxis layout

The following pre-existing files SHALL be moved as part of this change:

- `docs/cli-cookbook.md` → `docs/how-to/cli-cookbook.md`
- `docs/experiment-spec.md` → `docs/reference/experiment-spec.md`
- `docs/batch-spec.md` → `docs/reference/batch-spec.md` (marked "internal" at the top)
- `docs/optimization.md` → SPLIT into `docs/explanation/overfitting-and-selection.md` (theory), `docs/how-to/interpret-pbo-rejection.md` (ops), `docs/reference/objective-spec.md` (knobs)

Domain vocabulary currently inlined in `CLAUDE.md` SHALL be extracted to `docs/explanation/domain-vocabulary.md`; the `CLAUDE.md` block becomes a one-line pointer to that file. `README.md` SHALL be trimmed to elevator pitch + one architecture diagram + links into `docs/`.

#### Scenario: Moved files keep working content

- **WHEN** `mkdocs build --strict` runs after the move
- **THEN** every link from README, CLAUDE.md, openspec specs, and other docs into the moved files resolves, with no 404s

#### Scenario: Split of optimization.md preserves substance

- **WHEN** a reviewer compares the union of the three split files against the original `docs/optimization.md`
- **THEN** all theory text appears in `overfitting-and-selection.md`, all operator-facing how-to appears in `interpret-pbo-rejection.md`, and all configuration knob reference appears in `objective-spec.md`, with no content lost

### Requirement: README and CLAUDE.md retain operating contracts

`README.md` SHALL serve as the elevator pitch and entry point: short project description, the one-screen architecture diagram, links into `docs/`. It MUST NOT host long-form reference, how-to, or methodology content. `CLAUDE.md` SHALL retain the agent contract — purpose, architecture, repo layout, module roles, build/lint/env — but MUST replace its detailed Domain vocabulary block with a pointer to `docs/explanation/domain-vocabulary.md`.

#### Scenario: README is short

- **WHEN** `README.md` is measured after this change
- **THEN** it is substantially shorter than its pre-change size and contains links into `docs/` for content that previously lived inline

#### Scenario: CLAUDE.md still answers agent setup questions

- **WHEN** a fresh agent reads `CLAUDE.md`
- **THEN** it can locate every module, the lint command, the env vars, and finds Domain vocabulary via one pointer to `docs/explanation/domain-vocabulary.md`

### Requirement: CI workflow gates docs

The repository SHALL include `.github/workflows/docs.yml` that runs `mkdocs build --strict` on pull requests targeting `main` or any `release/v*` branch. On push to those same branches it MUST deploy via `mike`. The job MUST install dependencies from `requirements-docs.txt`.

#### Scenario: PR fails on broken links

- **WHEN** a PR contains a broken `.md` cross-reference
- **THEN** the docs CI job exits non-zero and the PR is blocked from merging

#### Scenario: Push to main triggers deploy

- **WHEN** a commit is pushed to `main`
- **THEN** the workflow deploys the `dev` slot to `gh-pages` via `mike`

### Requirement: Makefile exposes docs targets

The `Makefile` SHALL expose at least `docs-serve` (runs `mkdocs serve`) and `docs-build` (runs `mkdocs build --strict`). `make lint` MUST invoke `mkdocs build --strict` as part of its suite so that broken links and missing references fail the lint gate.

#### Scenario: Local serve works

- **WHEN** a contributor runs `make docs-serve`
- **THEN** MkDocs starts and serves the site at `http://127.0.0.1:8000`

#### Scenario: Lint catches broken docs

- **WHEN** a `.md` file under `docs/` references a missing path and a contributor runs `make lint`
- **THEN** lint exits non-zero with the docs build error

### Requirement: Tutorial pages follow a fixed skeleton

Every page under `docs/tutorials/` SHALL include the following sections, in this order, before any optional content: a one-sentence **Learning goal**, a **Prerequisites** bullet list, a **Walkthrough** of numbered steps where each step states the command run and the expected output snippet, a **What you just did** paragraph naming the components the walkthrough exercised, and a **What next** bullet list of links into the `how-to/`, `reference/`, and `explanation/` quadrants. Additional sections MAY appear after **What next**, but the five required sections MUST be present and ordered.

#### Scenario: Tutorial page conforms to the skeleton

- **WHEN** a new tutorial page is added under `docs/tutorials/`
- **THEN** the page contains the five required sections in the prescribed order, and code review accepts it

#### Scenario: Tutorial page missing a required section

- **WHEN** a tutorial page lacks any of `Learning goal`, `Prerequisites`, `Walkthrough`, `What you just did`, or `What next`
- **THEN** code review rejects the page until the missing section is added

### Requirement: Walkthrough steps pair command with expected output

Each numbered step in a tutorial **Walkthrough** SHALL pair the command the reader runs with the output snippet they should expect. The command MUST be in a fenced code block. The expected output snippet MUST be either inline or in a separate fenced block immediately following the command block. A step that has no observable output (e.g., `cd` into a directory) MUST state that explicitly rather than omit the output anchor.

#### Scenario: Step shows command and expected output

- **WHEN** a tutorial step describes running `strategy-gpt run --spec experiments/vxx.yaml`
- **THEN** the step contains both the command fence and a following block showing the expected stdout (or an explicit "no stdout output" note)

#### Scenario: Step without observable output

- **WHEN** a step is a directory change or environment setup with no stdout
- **THEN** the step states "no stdout output" (or equivalent) inline, instead of omitting the expected-output anchor

### Requirement: The repository ships at least four initial tutorials

The repository SHALL contain, at minimum, the following committed pages under `docs/tutorials/`: `first-backtest.md`, `author-a-strategy.md`, `running-an-optimization.md`, and `hypothesize-loop.md`. Each MUST conform to the tutorial skeleton requirement and MUST appear in the `mkdocs.yml` navigation under the Tutorials section. Adding tutorials beyond this initial set is permitted and additive.

#### Scenario: Initial tutorial set present

- **WHEN** `mkdocs build --strict` runs against a fresh checkout
- **THEN** `docs/tutorials/first-backtest.md`, `docs/tutorials/author-a-strategy.md`, `docs/tutorials/running-an-optimization.md`, and `docs/tutorials/hypothesize-loop.md` exist and build cleanly

#### Scenario: A tutorial in the initial set is removed

- **WHEN** a pull request removes any of `first-backtest.md`, `author-a-strategy.md`, `running-an-optimization.md`, or `hypothesize-loop.md`
- **THEN** code review rejects the removal unless the change is paired with a spec amendment that drops the page from the initial set

### Requirement: The hypothesize-loop tutorial exercises the CLI surface end-to-end

`docs/tutorials/hypothesize-loop.md` SHALL walk the reader through, in order, `strategy-gpt hypothesize <strategy> --dry-run`, `strategy-gpt hypothesis replay <decision_id>`, and `strategy-gpt hypothesis diff <decision_id>`. The walkthrough MUST run against a per-strategy ledger generated from the in-repo smoke driver or shipped stub fixture (no `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` required). Each numbered step MUST pair the command with the expected output snippet per the existing **Walkthrough steps pair command with expected output** requirement.

#### Scenario: Tutorial reader has no LLM API keys

- **WHEN** a reader follows `docs/tutorials/hypothesize-loop.md` from a fresh clone with neither `ANTHROPIC_API_KEY` nor `OPENAI_API_KEY` set
- **THEN** every numbered step completes without error and the printed outputs match the snippets shown in the page

#### Scenario: Tutorial demonstrates all three documented commands

- **WHEN** `docs/tutorials/hypothesize-loop.md` is rendered
- **THEN** at least one numbered step in the **Walkthrough** invokes each of `strategy-gpt hypothesize`, `strategy-gpt hypothesis replay`, and `strategy-gpt hypothesis diff`

### Requirement: Tutorials index links only to committed pages

`docs/tutorials/index.md` SHALL contain links only to tutorial pages that exist as committed files in `docs/tutorials/`. Forward-looking placeholders ("coming soon", `TODO`, broken anchors, or links to files not yet in the repository) MUST NOT appear in the tutorials index.

#### Scenario: Index entry resolves to a committed page

- **WHEN** `docs/tutorials/index.md` lists an entry
- **THEN** the link target resolves to a `.md` file under `docs/tutorials/` and `mkdocs build --strict` does not warn on it

#### Scenario: Placeholder entry rejected at review

- **WHEN** a pull request adds a "coming soon" or `TODO` entry to `docs/tutorials/index.md`
- **THEN** code review rejects the entry and the contributor either lands the linked page in the same PR or omits the entry

### Requirement: Tutorial walkthroughs use the bundled reference strategy or stub data

Tutorial walkthroughs SHALL use the bundled VXX reference strategy under `crates/vxx-strategy/` or self-contained stub data declared in the page when running commands. A tutorial MUST NOT depend on an external dataset, third-party API key, or a non-default provider unless the **Prerequisites** section explicitly names the dependency and the walkthrough either (a) provides a stub-data fallback so the reader can complete the tutorial without it, or (b) for tutorials that exercise an LLM-driven command surface (e.g. `strategy-gpt author`), structures the walkthrough as a read-along that pairs an illustrative example invocation with a description of the *shape* of expected output, so the reader can complete the page by reading and inspecting referenced files without needing to reproduce a transcript byte-for-byte.

#### Scenario: Tutorial runs against the bundled strategy

- **WHEN** a tutorial walkthrough invokes `strategy-gpt run` or `strategy-gpt optimize`
- **THEN** the command targets `crates/vxx-strategy/` or stub data shipped in the repository, not a remote dataset or a user-private crate

#### Scenario: Tutorial relies on an optional external dependency

- **WHEN** a tutorial walkthrough depends on an external service (e.g., a paid data feed)
- **THEN** the **Prerequisites** section names the dependency and the walkthrough provides a stub-data fallback so the reader can complete the tutorial without it

#### Scenario: Tutorial exercises an LLM-driven command

- **WHEN** a tutorial walkthrough exercises an LLM-driven command (such as `strategy-gpt author`)
- **THEN** the **Prerequisites** section names the required `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` and the walkthrough explicitly states that the dialog transcript is illustrative — the load-bearing outcome is the shape of the on-disk artifacts the command produces

### Requirement: Author tutorial walks the `strategy-gpt author` flow end-to-end

`docs/tutorials/author-a-strategy.md` SHALL walk the reader through invoking `strategy-gpt author` with a natural-language seed, navigating the clarifying-question dialog, and inspecting the on-disk crate artifacts (`Cargo.toml`, `src/lib.rs`, `smoke.toml`, `intent.toml`) the run produces under `crates/<name>-strategy/`. The page MUST conform to the existing tutorial skeleton (`## Learning goal`, `## Prerequisites`, `## Walkthrough`, `## What you just did`, `## What next`). The walkthrough MUST illustrate dialog turns rather than require them to match byte-for-byte, and MUST state explicitly that the LLM's exact wording is non-load-bearing.

#### Scenario: Tutorial opens with the canonical skeleton

- **WHEN** `docs/tutorials/author-a-strategy.md` is rendered
- **THEN** it contains, in order, top-level H2 sections `Learning goal`, `Prerequisites`, `Walkthrough`, `What you just did`, and `What next`

#### Scenario: Tutorial exercises the author command itself

- **WHEN** the `Walkthrough` section is read
- **THEN** at least one numbered step invokes `strategy-gpt author` with a positional natural-language seed, and at least one subsequent step inspects the emitted files under `crates/<name>-strategy/`

#### Scenario: Tutorial calls out dialog non-determinism

- **WHEN** the reader follows the dialog section of the walkthrough
- **THEN** the page contains explicit prose stating that the LLM's clarifying questions will vary across runs and that the load-bearing outcome is the shape of the on-disk artifacts, not the dialog transcript

### Requirement: Hand-authored strategy guide lives in the Explanation quadrant

The strategy-trait deep dive currently at `docs/tutorials/authoring-a-strategy.md` SHALL be relocated to `docs/explanation/hand-authoring-a-strategy.md`. The relocated page MUST preserve the trait-implementation walkthrough (`Strategy` impl, `params_schema.json`, `cargo build`, end-to-end `strategy-gpt run`) and MUST reframe its introduction to position the page as engineer-targeted reference for extending the trait surface or debugging an author-emitted crate — not as the recommended creation path.

#### Scenario: Page exists at the new location

- **WHEN** `mkdocs build --strict` runs after the change
- **THEN** `docs/explanation/hand-authoring-a-strategy.md` exists and renders without warnings, and `docs/tutorials/authoring-a-strategy.md` no longer exists

#### Scenario: Reframed intro references the author command

- **WHEN** the first prose paragraph of `docs/explanation/hand-authoring-a-strategy.md` is read
- **THEN** it identifies `strategy-gpt author` as the default path for creating a strategy and frames the page as the engineer-targeted deep dive into what the author command emits

### Requirement: CLI cookbook covers the `author` command surface

`docs/how-to/cli-cookbook.md` SHALL contain a top-level section dedicated to the `author` command, mirroring the cookbook's house style. The section MUST cover, at minimum: invoking `author` with and without a positional seed, the `--verify=batch` flag, the `--k-repair-emit` / `--k-repair-build` budget overrides, the edit-mode trigger (re-running `author` against an existing crate name), and at least one troubleshooting recipe for a budget-exhaustion or non-whitelisted-dep failure.

#### Scenario: Cookbook has an Author section

- **WHEN** `docs/how-to/cli-cookbook.md` is rendered
- **THEN** it contains an H2 section whose heading begins with the word `Author` and whose body documents the items enumerated in the requirement

#### Scenario: Cookbook section cross-links the tutorial and how-to

- **WHEN** the cookbook's Author section is rendered
- **THEN** it links to both `docs/tutorials/author-a-strategy.md` and `docs/how-to/author-a-strategy.md`

### Requirement: Tutorials and how-to index pages surface the author surface

`docs/tutorials/index.md` SHALL list the new author tutorial in its tutorial roster. `docs/how-to/index.md` SHALL list the author how-to in its how-to roster. Both index entries MUST appear under the same headings the rest of the entries do (no special-casing).

#### Scenario: Tutorials index lists the author tutorial

- **WHEN** `docs/tutorials/index.md` is rendered
- **THEN** it links to `author-a-strategy.md` under the same section the other tutorial entries occupy

#### Scenario: How-to index lists the author how-to

- **WHEN** `docs/how-to/index.md` is rendered
- **THEN** it links to `author-a-strategy.md` under the same section the other how-to entries occupy
