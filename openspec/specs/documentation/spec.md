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
