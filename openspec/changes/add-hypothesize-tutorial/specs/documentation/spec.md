## MODIFIED Requirements

### Requirement: The repository ships at least four initial tutorials

The repository SHALL contain, at minimum, the following committed pages under `docs/tutorials/`: `first-backtest.md`, `authoring-a-strategy.md`, `running-an-optimization.md`, and `hypothesize-loop.md`. Each MUST conform to the tutorial skeleton requirement and MUST appear in the `mkdocs.yml` navigation under the Tutorials section. Adding tutorials beyond this initial set is permitted and additive.

#### Scenario: Initial tutorial set present

- **WHEN** `mkdocs build --strict` runs against a fresh checkout
- **THEN** `docs/tutorials/first-backtest.md`, `docs/tutorials/authoring-a-strategy.md`, `docs/tutorials/running-an-optimization.md`, and `docs/tutorials/hypothesize-loop.md` exist and build cleanly

#### Scenario: A tutorial in the initial set is removed

- **WHEN** a pull request removes any of `first-backtest.md`, `authoring-a-strategy.md`, `running-an-optimization.md`, or `hypothesize-loop.md`
- **THEN** code review rejects the removal unless the change is paired with a spec amendment that drops the page from the initial set

## ADDED Requirements

### Requirement: The hypothesize-loop tutorial exercises the CLI surface end-to-end

`docs/tutorials/hypothesize-loop.md` SHALL walk the reader through, in order, `strategy-gpt hypothesize <strategy> --dry-run`, `strategy-gpt hypothesis replay <decision_id>`, and `strategy-gpt hypothesis diff <decision_id>`. The walkthrough MUST run against a per-strategy ledger generated from the in-repo smoke driver or shipped stub fixture (no `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` required). Each numbered step MUST pair the command with the expected output snippet per the existing **Walkthrough steps pair command with expected output** requirement.

#### Scenario: Tutorial reader has no LLM API keys

- **WHEN** a reader follows `docs/tutorials/hypothesize-loop.md` from a fresh clone with neither `ANTHROPIC_API_KEY` nor `OPENAI_API_KEY` set
- **THEN** every numbered step completes without error and the printed outputs match the snippets shown in the page

#### Scenario: Tutorial demonstrates all three documented commands

- **WHEN** `docs/tutorials/hypothesize-loop.md` is rendered
- **THEN** at least one numbered step in the **Walkthrough** invokes each of `strategy-gpt hypothesize`, `strategy-gpt hypothesis replay`, and `strategy-gpt hypothesis diff`
