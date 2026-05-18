# 0015 — Docs platform: MkDocs Material + mike + GitHub Pages

## Context

Strategy-gpt's docs serve two distinct readers — quants who need to operate the system and reason about methodology, and engineers who need to extend the platform — but until this change the docs were a 429-line README plus four scattered reference files with no navigation, no full-text search, no audience seams, no methodology rigor, and no rendered site. Refactors regularly broke cross-references and nothing caught them.

## Decision

Adopt **MkDocs Material** as the docs platform, **mike** for release-branch driven versioning, **GitHub Pages** for hosting. Structure docs under the **Diátaxis** framework (`tutorials/`, `how-to/`, `reference/`, `explanation/`) with `for-quants/` and `for-engineers/` as curated reading-path indexes — no content duplication. Methodology pages follow a fixed skeleton (intuition → formalism → worked example with synthetic toy data → assumptions → limitations → references) with the Limitations section mandatory. Cite via a central [bibliography](../explanation/bibliography.md). Math renders via `pymdownx.arithmatex` + MathJax. Search is the built-in lunr. ADRs (this one included) live at `docs/decisions/<NNNN>-<slug>.md`.

`mkdocs build --strict` gates `make lint` and pre-commit so broken doc links fail CI alongside rustfmt/clippy/ruff/mypy.

## Consequences

- Two reader audiences served by one source tree, navigated by intent.
- Site auto-deploys on push to `main` (`dev` slot) and `release/v*` (`vX.Y` slots). Tags are markers only.
- Pre-1.0 cost: cherry-pick discipline when patching docs on release branches. Owner accepts.
- Methodology pages cannot ship without honest limitations; bibliography is the single source of citation truth.
- Practitioner-grade voice is now the standard — internal-team-only voice will not get past review.

## Alternatives Considered

- **mdBook.** Rust-pure, but weaker nested nav, no native tabs, harder ADR/audience indexing.
- **Docusaurus.** MDX power not needed; node_modules drag.
- **Read the Docs (Sphinx).** Sphinx-centric, MkDocs second-class, RTD config pollutes repo.
- **Markdown-only, no site.** Cheapest but loses search, navigation, audience filters, and link-checking gate.
- **Tag-driven versioning.** Considered. Rejected because doc-only patches after a release benefit from re-deploying the same minor slot without ritual; release branches make this trivial.

## Status

accepted
