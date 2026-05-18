## Why

Strategy-gpt has two distinct readers — quants who need to operate the system and reason about methodology, and engineers who need to extend the platform — but current docs are a 429-line README plus scattered reference files with no clear navigation, no audience seams, no methodology rigor, and no captured rationale for load-bearing architectural decisions. As the project moves past prototype, this hurts onboarding, drifts under refactor, and makes the methodology (PBO, DSR, fold schemes) hard to trust without grep archaeology.

## What Changes

- Adopt **MkDocs Material** as the docs platform, **mike** for versioning, published to **GitHub Pages**.
- Restructure `docs/` under the **Diátaxis** framework: `tutorials/`, `how-to/`, `reference/`, `explanation/`.
- Introduce audience reading paths (`for-quants/`, `for-engineers/`) as curated indexes over the same content, no duplication.
- Move existing docs into the new layout:
  - `docs/cli-cookbook.md` → `docs/how-to/cli-cookbook.md`
  - `docs/experiment-spec.md` → `docs/reference/experiment-spec.md`
  - `docs/batch-spec.md` → `docs/reference/batch-spec.md` (marked internal)
  - `docs/optimization.md` → split into `explanation/overfitting-and-selection.md` (theory) + `how-to/interpret-pbo-rejection.md` (ops) + `reference/objective-spec.md` (knobs)
  - Domain vocabulary from `CLAUDE.md` → `docs/explanation/domain-vocabulary.md`
- Enforce a **methodology page skeleton** for every `explanation/` page covering a quant method: intuition → formalism → worked example (synthetic toy data) → assumptions → **limitations (mandatory)** → references.
- Add a **central bibliography** at `docs/explanation/bibliography.md` with anchor-linkable entries (Bailey/López de Prado et al.).
- Enable **LaTeX math** via `pymdownx.arithmatex` and **built-in lunr search**.
- Add **ADRs** at `docs/decisions/<NNNN>-<slug>.md` and **backfill** load-bearing existing decisions (Rust execution layer, Python orchestration, PyO3 boundary, worker subprocess + Arrow IPC, no sandboxing, sealed `Strategy` trait, SQLite+parquet ledger, hybrid graph+vector KB, year-segmented content-addressed cache, abort-on-failure batches, PBO default 0.5, mean as only OOS aggregator, Rust 1.82 pin, lint stance, docs platform itself).
- Versioning via **release branches**: each `release/vX.Y` branch drives a mike version slot; tags are markers only; `dev` slot tracks `main`; `latest` alias points at highest minor.
- CI: GitHub Actions workflow `docs.yml` runs `mkdocs build --strict` on PR and `mike deploy` on push to `main` or `release/v*`. Lint integrates `mkdocs build --strict` as a link-checker.
- Trim `README.md` to elevator pitch + one architecture diagram + links into docs.
- **Out of scope (deferred)**: research output / decision-log surface, PR previews, custom domain, Algolia search, docs translations.

## Capabilities

### New Capabilities
- `documentation`: docs platform contract — Diátaxis structure, audience reading paths, methodology page skeleton, ADR convention, bibliography rules, versioning model (release-branch driven), search/math/theme configuration, CI link-check gate.

### Modified Capabilities
- `lint-and-precommit`: add `mkdocs build --strict` to the lint suite so broken doc links fail CI alongside rustfmt/clippy/ruff/mypy.

## Impact

- **Affected code**: none in `crates/` or `python/`. Docs-only.
- **New files**: `mkdocs.yml`, `requirements-docs.txt`, `.github/workflows/docs.yml`, full `docs/` reorganization, `docs/decisions/<NNNN>-*.md` ADR backfill set, `docs/explanation/bibliography.md`.
- **Moved files**: existing `docs/*.md` relocate into Diátaxis quadrants. No external link consumers yet, no redirect ritual required.
- **`README.md`**: trimmed.
- **`CLAUDE.md`**: domain vocabulary extracted to docs; CLAUDE.md retains a one-line pointer.
- **`Makefile`**: new `docs-serve`, `docs-build` targets; `make lint` includes `mkdocs build --strict`.
- **Dependencies**: `mkdocs-material`, `mike`, `pymdownx.arithmatex`, plus extensions pinned in `requirements-docs.txt`. Python-only; no Rust impact.
- **Branching policy**: future `release/vX.Y` branches become first-class; CI must distinguish `main` push from `release/*` push.
- **Pre-1.0 stance**: only `dev` slot active at proposal time; first `release/v0.X` cut by the team triggers first frozen version.
