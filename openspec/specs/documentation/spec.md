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

- **WHEN** a `.md` page is added to `docs/` outside the four quadrant directories (excluding `index.md`, audience reading paths, decisions, the bibliography, and the guided CLI walkthrough at `docs/guided-cli-walkthrough.md`)
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

### Requirement: Guided CLI walkthrough exists at top level

The repository SHALL provide a guided CLI walkthrough at `docs/guided-cli-walkthrough.md`. The page MUST sit at the top level of `docs/` (outside the four Diátaxis quadrants), MUST appear in the `mkdocs.yml` nav as a top-level entry positioned before the four quadrant groups, and MUST be linked from `docs/index.md` as the primary entry point into the CLI surface.

#### Scenario: Walkthrough page exists at the documented path

- **WHEN** `mkdocs build --strict` runs
- **THEN** `docs/guided-cli-walkthrough.md` exists, renders without warnings, and is reachable via the top-level nav

#### Scenario: Home page surfaces the walkthrough

- **WHEN** `docs/index.md` is rendered
- **THEN** it links to `guided-cli-walkthrough.md` with framing that identifies the page as the recommended starting point for operators

#### Scenario: Nav positions the walkthrough above the Diátaxis groups

- **WHEN** `mkdocs.yml` nav is inspected
- **THEN** the entry for `guided-cli-walkthrough.md` appears above the `Tutorials`, `How-to`, `Reference`, and `Explanation` groups

### Requirement: Guided CLI walkthrough covers the full CLI surface stage by stage

The walkthrough SHALL be organized into nine numbered stages mirroring the operator's usage arc. Each stage MUST cover the CLI subcommands and flags relevant to that stage, written as prose framing plus fenced command snippets plus short explanations (not wide tables). A subcommand MAY appear in more than one stage when it is genuinely useful in more than one place.

The stages and their minimum CLI coverage are:

- **Stage 0 — Setup**: `strategy-gpt version`; the operator-facing build steps `cd crates && cargo build -p engine-worker` and `cd crates && cargo build -p vxx-strategy`; the Python bindings step `maturin develop -m crates/py-bindings/Cargo.toml`; the required env vars (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`); the recommended `RUSTC_WRAPPER=sccache`. The stage MUST explicitly state that lint / test / pre-commit / `cargo check --workspace` are out of scope (contributor tooling, not operator tooling).
- **Stage 1 — Explore**: `strategy-gpt --help`; per-subcommand help (`strategy-gpt <cmd> --help`); preview wiring with `strategy-gpt hypothesize <name> --baseline-defaults --dry-run`; preview optimize cost with `strategy-gpt optimize --spec ... --benchmark --sample 3 --yes`.
- **Stage 2 — Acquire data**: `strategy-gpt fetch` with the yfinance provider, with the `my_csv` provider, and across the four `--mode` values (`prefer_cache`, `validate`, `force_refresh`, `offline`); `strategy-gpt cache-stats`; the Python snippet that materializes bars to JSON for `strategy-gpt run`.
- **Stage 3 — Author**: `strategy-gpt author` with and without a positional seed; edit-mode (re-running against an existing crate name); `--verify=batch`; the repair-budget flags `--k-repair-emit` and `--k-repair-build`; `--model`, `--quiet`, `--verbose`; the paste-aware multi-line input via `<<<` / `>>>` sentinels; the repair-exhaustion menu's four options.
- **Stage 4 — One-shot backtest**: `strategy-gpt run --spec ... --wait`; submit-without-wait usage; editing `runs[].params` to tweak parameters without recompiling; multi-run sweep via additional `runs[]` entries.
- **Stage 5 — Optimize**: `strategy-gpt optimize` with the default method and with explicit `--method` overrides for `grid`, `random`, `sobol`, `recursive_grid`, `lhs_polish`, `successive_halving`, `cma_es`, and `differential_evolution`; `--benchmark --sample N --yes`; `--parallelism`; `strategy-gpt optimize inspect <opt_id>` and `inspect <opt_id> --trial <id>`; `strategy-gpt optimize replay <opt_id> --trial <id>`; `strategy-gpt optimize reselect <opt_id>` with `--pbo-threshold` and `--robust-objective`; `strategy-gpt optimize compare <opt_id> <a> <b>`; `--force` to publish despite a PBO rejection.
- **Stage 6 — Hypothesize + KB**: `strategy-gpt hypothesize <name>` with `--baseline-defaults` and `--baseline-from <opt-id>`; `--objective`; the per-stage model flags (`--model-stage1`, `--model-stage2`, `--model-stage3`, `--model-critique`, `--model-rank`); `--quick`, `--quiet`, `--dry-run`; the KB store flags `--kb-store` and `--rebuild-kb`; `strategy-gpt recent-decisions`; `strategy-gpt hypothesis replay <decision-id>`; `strategy-gpt hypothesis diff <decision-id>`; the `--strategy` scope flag for `hypothesis replay`/`diff`.
- **Stage 7 — Iterate**: the loop of re-optimizing after an accepted hypothesis, then re-running `hypothesize ... --baseline-from <new opt_id>` with the updated baseline; auditing accumulated decisions via `recent-decisions`.
- **Stage 8 — Reproduce / debug**: `strategy-gpt replay --run-id <ledger-run-id>`; `strategy-gpt optimize replay <opt_id> --trial <id>` (cross-reference back to Stage 5); the `offline` cache mode for reproducibility-sensitive CI; the byte-identity guarantee predicated on `(artifact_hash, dataset_manifest, params, seed, runner_version)`.

#### Scenario: Each stage exists and is numbered

- **WHEN** `docs/guided-cli-walkthrough.md` is rendered
- **THEN** it contains nine H2 sections numbered 0 through 8, each with a stable anchor of the form `stage-N-<slug>` (e.g. `stage-3-author`, `stage-5-optimize`)

#### Scenario: Stage 3 covers the author CLI surface

- **WHEN** Stage 3 is rendered
- **THEN** it documents `strategy-gpt author` invoked with and without a positional seed, the `--verify=batch` flag, the `--k-repair-emit` / `--k-repair-build` budget overrides, the edit-mode trigger, the paste-aware multi-line input, the repair-exhaustion menu, and at least one troubleshooting recipe (e.g. `smoke_failed: no_trades` or `exhausted repair budget`)

#### Scenario: Stage 6 covers hypothesis replay and diff

- **WHEN** Stage 6 is rendered
- **THEN** it documents `strategy-gpt hypothesis replay <decision-id>` and `strategy-gpt hypothesis diff <decision-id>`, including the `--strategy` scope flag and a one-sentence description of the JSON summary output and the unified-diff output respectively

#### Scenario: Each stage links to its depth pages

- **WHEN** any stage section is rendered
- **THEN** it contains a "See also" subsection linking to the relevant existing how-to / tutorial / reference / explanation pages for depth (e.g. Stage 5 links to `reference/objective-spec.md` and `how-to/interpret-pbo-rejection.md`; Stage 6 links to `how-to/run-hypothesize.md`; Stage 4 links to `reference/experiment-spec.md` and `reference/batch-spec.md`)

#### Scenario: Stage 0 disclaims contributor tooling

- **WHEN** Stage 0 is rendered
- **THEN** it includes one sentence stating that `make lint`, `make test`, `pre-commit`, and `cargo check --workspace` are contributor / CI commands rather than operator commands, with a pointer to the contributor docs

### Requirement: Cross-cutting CLI reminders are split per concern and placed contextually

The walkthrough SHALL split the cross-cutting CLI reminders into one snippet per concern, each living under `docs/_includes/` and each embedded via the mkdocs-material snippets directive (`--8<-- "<name>.md"`) at most once on the page — placed immediately after the first command snippet that makes the reminder load-bearing, not appended to every stage. The `pymdownx.snippets` extension MUST be enabled in `mkdocs.yml` with a `base_path` that resolves the `_includes/` directory.

The required snippets are:

- `docs/_includes/cli-progress.md` — covers the `--progress {auto,plain,json,off}` flag and its default behavior across long-running commands. Placed after the first long-running command in the walkthrough (Stage 2, the first `fetch` invocation).
- `docs/_includes/cli-env-keys.md` — covers the LLM-stage env vars `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` and which commands consume them. Placed at Stage 3, after the first `strategy-gpt author` invocation.
- `docs/_includes/cli-roots.md` — covers the configurable root flags `--cache-root`, `--ledger-root`, `--gateway-root`, `--work-root`. Placed after the first command in the walkthrough that uses a root flag (Stage 2, the first `fetch` invocation).

The recommended `RUSTC_WRAPPER=sccache` is covered inline in Stage 0's setup snippets rather than via an include, because it is a one-time env-var export rather than a per-command reminder.

#### Scenario: Snippet files exist at the documented paths

- **WHEN** `mkdocs build --strict` runs
- **THEN** `docs/_includes/cli-progress.md`, `docs/_includes/cli-env-keys.md`, and `docs/_includes/cli-roots.md` exist and resolve via the configured snippets `base_path`

#### Scenario: Each snippet is embedded once at its contextual home

- **WHEN** the walkthrough is rendered
- **THEN** each of the three snippets appears exactly once, immediately following the first command snippet that makes the reminder load-bearing (progress + roots after the first `fetch` in Stage 2; env keys after the first `author` in Stage 3); no stage appends the reminders out of context

#### Scenario: Snippets extension is enabled

- **WHEN** `mkdocs.yml` is inspected
- **THEN** the `markdown_extensions` list includes `pymdownx.snippets` with a `base_path` setting that includes `docs/_includes/`

### Requirement: Inbound links to the removed cookbook are repointed

Every page under `docs/` that previously linked to `docs/how-to/cli-cookbook.md` (with or without an anchor fragment) SHALL be updated in the same change that removes the cookbook. Each updated link MUST resolve to either `docs/guided-cli-walkthrough.md` or a specific stage anchor within it, chosen to land the reader on the equivalent content.

#### Scenario: No reference to the removed cookbook survives

- **WHEN** `grep -rln 'cli-cookbook' docs/` is run after the change
- **THEN** the command returns no matches

#### Scenario: All internal links resolve

- **WHEN** `mkdocs build --strict` runs after the change
- **THEN** the build exits zero with no broken-link warnings

### Requirement: Tutorials and how-to index pages surface the author surface

`docs/tutorials/index.md` SHALL list the new author tutorial in its tutorial roster. `docs/how-to/index.md` SHALL list the author how-to in its how-to roster. Both index entries MUST appear under the same headings the rest of the entries do (no special-casing).

#### Scenario: Tutorials index lists the author tutorial

- **WHEN** `docs/tutorials/index.md` is rendered
- **THEN** it links to `author-a-strategy.md` under the same section the other tutorial entries occupy

#### Scenario: How-to index lists the author how-to

- **WHEN** `docs/how-to/index.md` is rendered
- **THEN** it links to `author-a-strategy.md` under the same section the other how-to entries occupy
