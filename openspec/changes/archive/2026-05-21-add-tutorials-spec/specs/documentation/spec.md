## ADDED Requirements

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

### Requirement: The repository ships at least three initial tutorials

The repository SHALL contain, at minimum, the following committed pages under `docs/tutorials/`: `first-backtest.md`, `authoring-a-strategy.md`, and `running-an-optimization.md`. Each MUST conform to the tutorial skeleton requirement and MUST appear in the `mkdocs.yml` navigation under the Tutorials section. Adding tutorials beyond this initial set is permitted and additive.

#### Scenario: Initial tutorial set present

- **WHEN** `mkdocs build --strict` runs against a fresh checkout
- **THEN** `docs/tutorials/first-backtest.md`, `docs/tutorials/authoring-a-strategy.md`, and `docs/tutorials/running-an-optimization.md` exist and build cleanly

#### Scenario: A tutorial in the initial set is removed

- **WHEN** a pull request removes any of `first-backtest.md`, `authoring-a-strategy.md`, or `running-an-optimization.md`
- **THEN** code review rejects the removal unless the change is paired with a spec amendment that drops the page from the initial set

### Requirement: Tutorials index links only to committed pages

`docs/tutorials/index.md` SHALL contain links only to tutorial pages that exist as committed files in `docs/tutorials/`. Forward-looking placeholders ("coming soon", `TODO`, broken anchors, or links to files not yet in the repository) MUST NOT appear in the tutorials index.

#### Scenario: Index entry resolves to a committed page

- **WHEN** `docs/tutorials/index.md` lists an entry
- **THEN** the link target resolves to a `.md` file under `docs/tutorials/` and `mkdocs build --strict` does not warn on it

#### Scenario: Placeholder entry rejected at review

- **WHEN** a pull request adds a "coming soon" or `TODO` entry to `docs/tutorials/index.md`
- **THEN** code review rejects the entry and the contributor either lands the linked page in the same PR or omits the entry

### Requirement: Tutorial walkthroughs use the bundled reference strategy or stub data

Tutorial walkthroughs SHALL use the bundled VXX reference strategy under `crates/vxx-strategy/` or self-contained stub data declared in the page when running commands. A tutorial MUST NOT depend on an external dataset, third-party API key, or a non-default provider unless the **Prerequisites** section explicitly names the dependency and the walkthrough degrades to a stub-data path when the dependency is absent.

#### Scenario: Tutorial runs against the bundled strategy

- **WHEN** a tutorial walkthrough invokes `strategy-gpt run` or `strategy-gpt optimize`
- **THEN** the command targets `crates/vxx-strategy/` or stub data shipped in the repository, not a remote dataset or a user-private crate

#### Scenario: Tutorial relies on an optional external dependency

- **WHEN** a tutorial walkthrough depends on an external service (e.g., a paid data feed)
- **THEN** the **Prerequisites** section names the dependency and the walkthrough provides a stub-data fallback so the reader can complete the tutorial without it
