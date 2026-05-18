## Context

Strategy-gpt today has 1.8k lines of docs split across `README.md` (429 lines, mixed pitch + arch + quickstart), four files in `docs/` (experiment-spec, batch-spec, optimization, cli-cookbook), and per-capability normative specs in `openspec/specs/`. There is no nav tree, no full-text search, no audience seam, no captured rationale for load-bearing architectural decisions, and no rendered site. Two readers must coexist: quants (research methodology, vocabulary, results interpretation) and platform engineers (trust boundaries, module contracts, build pipeline). The work below is documentation-only — no `crates/` or `python/strategy_gpt/` source changes.

## Goals / Non-Goals

**Goals:**

- Single source-of-truth for *operating* strategy-gpt, GitHub-browseable as raw markdown AND deployable as a versioned static site.
- Diátaxis quadrants (`tutorials/`, `how-to/`, `reference/`, `explanation/`) with audience reading paths (`for-quants/`, `for-engineers/`) — no content duplication, only curated indexes.
- Practitioner-grade methodology pages with mandatory limitations sections and central bibliography.
- Captured load-bearing architectural decisions in standalone ADR files; backfill before proposal merges.
- Release-branch driven versioning via `mike` so historic releases keep their docs.
- CI gate that catches broken internal links so docs cannot rot under refactor.

**Non-Goals:**

- Research output surface (decision log, hypothesis ledger renderings) — deferred.
- Algolia DocSearch, PR previews, custom domain, doc translations.
- Live trading concepts; everything stays research-platform framed.
- Rewriting the substance of existing docs — this change moves and re-quadrants them, content rewrites happen incrementally afterward.
- Touching `openspec/specs/*` content (the openspec capability files remain the normative contract for code).

## Decisions

### Decision 1: MkDocs Material over mdBook / Docusaurus / RTD

**Choice**: MkDocs Material.

**Rationale**: Python toolchain already first-class in the repo; native mermaid + admonitions + content tabs; `mkdocs.yml` nav supports audience reading paths without duplicating content; fast build (<5s); ecosystem alignment with FastAPI/Pydantic/Material precedent.

**Alternatives considered**:
- **mdBook** — Rust-pure, but weaker nested nav, no native tabs, ADR/audience indexing harder.
- **Docusaurus** — overkill, MDX power not needed, node_modules drag.
- **Read the Docs (Sphinx)** — Sphinx-centric, MkDocs second-class, RTD config pollutes repo.

### Decision 2: Diátaxis structure with audience reading paths

**Choice**: Four quadrants under `docs/` plus `for-quants/index.md` and `for-engineers/index.md` as curated link indexes (no content duplication).

**Rationale**: Diátaxis is the convergent OSS docs pattern (Django, Numpy, Cloudflare, GitLab adopted it). Maps cleanly onto existing files (cli-cookbook = how-to, experiment-spec = reference, optimization = explanation+how-to+reference split). Audience reading paths solve the "who is this for" problem without forking content.

**Alternatives considered**:
- **Two separate trees** (`for-quants/` and `for-engineers/` each with their own tutorials/reference) — duplication risk, drift guaranteed.
- **Single flat tree** — current state; doesn't scale past current size.

### Decision 3: Methodology page skeleton enforced for `explanation/` pages covering quant methods

**Choice**: Required sections, in order: Intuition → Formalism → Worked example (synthetic toy data) → Assumptions → Limitations (mandatory) → References (→ bibliography.md).

**Rationale**: Practitioner-grade voice agreed in exploration. Forces honesty about when methods fail (PBO assumes IID folds, DSR assumes Gaussian returns). Synthetic toy data over VXX reference because VXX changes when strategy changes; toy fixtures stay stable.

**Alternatives considered**:
- **Internal-team voice** — undersells the methodology work, undermines quant trust.
- **Paper-grade with proofs** — busywork without external publication target.

### Decision 4: ADRs in `docs/decisions/<NNNN>-<slug>.md`, standalone

**Choice**: Standalone ADR directory, lightweight template (Context, Decision, Consequences, Alternatives, Status), backfill load-bearing existing decisions before this change archives.

**Rationale**: Decision rationale currently lives in commit messages, scattered openspec design docs, and CLAUDE.md tribal knowledge. Standalone ADRs are grep-friendly, GitHub-renderable, mkdocs-indexable. Lightweight template avoids ceremony.

**Backfill seed list** (numbering not load-bearing, finalized in tasks):
- Rust for execution layer (performance + memory safety)
- Python for orchestration (LangGraph/LangChain ecosystem maturity)
- PyO3 in-process for trusted crates
- Subprocess + Arrow IPC for engine workers
- Worker process isolation, no sandboxing
- Sealed `Strategy` trait, no backwards compat
- SQLite + parquet sidecars for ledger
- Hybrid graph+vector KB over SQLite-backed store
- Year-segmented content-addressed cache
- Abort-on-failure batch semantics
- PBO threshold default 0.5
- Mean as only OOS aggregator
- Rust 1.82.0 toolchain pin
- Lint stance (Rust tool-defaults, Python strict ruleset)
- This docs platform decision itself

**Alternatives considered**:
- **No ADRs** — rationale stays in git archaeology; high drift risk for dual-audience repo.
- **ADRs auto-emitted from openspec changes** — couples too tightly; not every decision passed through a change proposal.

### Decision 5: Release-branch driven versioning via `mike`

**Choice**: `release/vX.Y` branches drive `mike` version slots; tags are markers only. `dev` slot tracks `main`; `latest` alias points at the highest minor release branch.

**Rationale**: Allows post-release doc fixes (typo, clarification) on the same branch without re-tagging or freezing-then-thawing. Tags remain pure semantic markers consumed by humans, not by CI. `mike` deploys whatever ref is checked out — the workflow logic stays trivial (`on: push: branches: [main, 'release/v*']`).

**Patch-fix policy**: doc-only fixes commit directly to the release branch; CI redeploys the same slot. No `--update` ritual on tags. Divergence between `main` and release branches is intentional and manually managed via cherry-pick when desired.

**Alternatives considered**:
- **Tag-driven versioning** — immutable but adds friction for doc-only patches.
- **`main`-only** — pre-1.0 valid but loses history once first release cut.
- **`latest` + `dev` only** — too coarse; eventually want per-minor history.

### Decision 6: GitHub Pages hosting, lunr search, LaTeX via arithmatex

- **Hosting**: GitHub Pages, `gh-pages` branch managed by `mike`. Free, native, no new service.
- **Search**: Material's built-in lunr. Corpus is small (under ~5k lines projected), no Algolia justification.
- **Math**: `pymdownx.arithmatex` → MathJax. Renders LaTeX in HTML; GitHub raw view shows source `$...$` (acceptable degradation).

### Decision 7: Lint gate via `mkdocs build --strict`

**Choice**: `make lint` invokes `mkdocs build --strict` after the existing rustfmt/clippy/ruff/mypy suite. Strict mode fails on warnings, including broken internal links and missing referenced files.

**Rationale**: Docs are now a CI artifact. Refactors that rename modules, delete pages, or break cross-references must fail loudly. Adding to `make lint` keeps single-entry-point invariant.

**Trade-off**: Build cost on every lint run (~3-5s). Acceptable; can be skipped in fast pre-commit pass if needed.

### Decision 8: `docs/decisions/0015` (this proposal)

The docs platform decision itself is captured as an ADR backfilled in the same change. Pattern: when ADR backfill list includes "the decision establishing the ADR convention", the ADR is part of the same commit.

## Risks / Trade-offs

- **Release-branch divergence rot** → User accepts. Cherry-pick discipline is theirs.
- **mkdocs build added to lint slows CI marginally** → Acceptable; gate value > seconds cost. Can carve into a separate `make lint-docs` if hot-loop matters later.
- **ADR backfill is judgment-heavy** → Each backfilled ADR captures *current* understanding, not historical re-litigation. If history is muddy, the ADR records "as of this date, we operate as if X was decided because Y."
- **Methodology pages without code execution can drift from implementation** → Mitigated by mandatory worked example using a fixture; if the fixture changes, the page is touched. Long-term option: doctest-style execution of code snippets in CI (out of scope here).
- **Diátaxis can over-categorize** → Acceptable; pages can live in two quadrants via internal cross-links. Reading paths absorb edge cases.
- **`mike` learning curve** → Standard pattern, well-documented; one-time cost.
- **GitHub Pages outage risk** → Acceptable for pre-1.0 internal-team-primary readership.

## Migration Plan

1. Land mkdocs scaffold (`mkdocs.yml`, `requirements-docs.txt`, GitHub Actions workflow, Makefile targets) on a feature branch. `make lint` includes `mkdocs build --strict`.
2. Move existing `docs/*.md` files into Diátaxis quadrants in one commit per file (preserve `git mv` history); update relative links.
3. Split `docs/optimization.md` into the three target files (theory / ops / knobs).
4. Extract domain vocabulary from `CLAUDE.md` to `docs/explanation/domain-vocabulary.md`; replace in-place block with a one-line pointer.
5. Trim `README.md` to pitch + diagram + links.
6. Write the methodology page skeleton template and bibliography seed.
7. Backfill ADRs from the seed list; one ADR per commit with clear context lines.
8. Add audience reading-path indexes (`for-quants/index.md`, `for-engineers/index.md`) as curated link lists.
9. Verify `mkdocs serve` locally, `mkdocs build --strict` clean, push to a feature branch, confirm GitHub Pages preview deploy.
10. Merge to `main`; CI publishes `dev` slot.

No rollback plan required beyond `git revert` — change is documentation-only and additive.

## Open Questions

- ADR numbering: gap-free monotonic (0001..0015) or domain-prefixed (`arch-0001`, `process-0001`, `methodology-0001`)? Defer; default to flat monotonic unless reviewers prefer otherwise.
- Whether to seed `tutorials/` content in this change or only stand up the empty directory with an index. Default: empty + placeholder index, content in follow-up changes (this change is platform, not content).
- First `release/vX.Y` branch cut timing — owner of that decision lives outside this change.
- Whether the Makefile target should be `make docs` or `make docs-serve` + `make docs-build`. Default: both, plus alias `make docs` → `docs-serve`.
