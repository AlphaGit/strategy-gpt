## ADDED Requirements

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

## MODIFIED Requirements

### Requirement: The repository ships at least four initial tutorials

The repository SHALL contain, at minimum, the following committed pages under `docs/tutorials/`: `first-backtest.md`, `author-a-strategy.md`, `running-an-optimization.md`, and `hypothesize-loop.md`. Each MUST conform to the tutorial skeleton requirement and MUST appear in the `mkdocs.yml` navigation under the Tutorials section. Adding tutorials beyond this initial set is permitted and additive.

#### Scenario: Initial tutorial set present

- **WHEN** `mkdocs build --strict` runs against a fresh checkout
- **THEN** `docs/tutorials/first-backtest.md`, `docs/tutorials/author-a-strategy.md`, `docs/tutorials/running-an-optimization.md`, and `docs/tutorials/hypothesize-loop.md` exist and build cleanly

#### Scenario: A tutorial in the initial set is removed

- **WHEN** a pull request removes any of `first-backtest.md`, `author-a-strategy.md`, `running-an-optimization.md`, or `hypothesize-loop.md`
- **THEN** code review rejects the removal unless the change is paired with a spec amendment that drops the page from the initial set

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
