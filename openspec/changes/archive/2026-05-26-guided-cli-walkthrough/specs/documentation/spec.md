## MODIFIED Requirements

### Requirement: Documentation follows the Diátaxis quadrant structure

`docs/` SHALL be organized into four top-level quadrants: `tutorials/`, `how-to/`, `reference/`, `explanation/`. Each `.md` page MUST live in exactly one quadrant. The navigation defined in `mkdocs.yml` MUST reflect the same structure.

#### Scenario: Page lives in correct quadrant

- **WHEN** a new page is added under `docs/`
- **THEN** the file path matches one of `tutorials/`, `how-to/`, `reference/`, `explanation/` and the `mkdocs.yml` nav entry mirrors the path

#### Scenario: Page outside the four quadrants is rejected

- **WHEN** a `.md` page is added to `docs/` outside the four quadrant directories (excluding `index.md`, audience reading paths, decisions, the bibliography, and the guided CLI walkthrough at `docs/guided-cli-walkthrough.md`)
- **THEN** code review rejects the placement or the page is moved into a quadrant before merge

### Requirement: Existing documentation is relocated into the Diátaxis layout

The following pre-existing files SHALL be moved as part of the initial Diátaxis migration:

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

## REMOVED Requirements

### Requirement: CLI cookbook covers the `author` command surface

**Reason**: The cookbook (`docs/how-to/cli-cookbook.md`) is removed in favor of a journey-based guided walkthrough at `docs/guided-cli-walkthrough.md`. Stage 3 of the walkthrough covers the same author CLI surface (and more); the new "Guided CLI walkthrough covers the full CLI surface stage by stage" requirement supersedes this one.

**Migration**: Operators previously linked to `docs/how-to/cli-cookbook.md#author-a-strategy` should follow `docs/guided-cli-walkthrough.md#stage-3-author` instead. Tutorials and audience reading-path indexes are repointed in the same change that removes the cookbook.

## ADDED Requirements

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

### Requirement: Reusable cross-cutting snippet is embedded in every walkthrough stage

A single canonical reminder of cross-cutting CLI concerns (progress modes, env vars, root paths) SHALL exist at `docs/_includes/cli-cross-cutting.md`. Every stage of the guided walkthrough MUST embed this snippet via the mkdocs-material snippets directive (`--8<-- "_includes/cli-cross-cutting.md"`). The `pymdownx.snippets` extension MUST be enabled in `mkdocs.yml` with a `base_path` that resolves the `_includes/` directory.

The snippet MUST cover, at minimum:

- The `--progress {auto,plain,json,off}` flag and its default behavior across long-running commands.
- The LLM-stage env vars `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` and which commands consume them.
- The recommended `RUSTC_WRAPPER=sccache` for Rust rebuild speed.
- The configurable root flags `--cache-root`, `--ledger-root`, `--gateway-root`, `--work-root`.

#### Scenario: Snippet file exists at the documented path

- **WHEN** `mkdocs build --strict` runs
- **THEN** `docs/_includes/cli-cross-cutting.md` exists and resolves via the configured snippets `base_path`

#### Scenario: Every stage embeds the snippet

- **WHEN** Stages 0 through 8 are rendered
- **THEN** each stage contains the rendered output of `docs/_includes/cli-cross-cutting.md` (identical text across stages), confirming that no stage hand-copies a divergent version

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
