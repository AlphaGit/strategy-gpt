## 1. Platform scaffold

- [x] 1.1 Add `requirements-docs.txt` pinning `mkdocs`, `mkdocs-material`, `mike`, `pymdownx` extensions (verify each release is ≥7 days old per supply-chain rule)
- [x] 1.2 Add `mkdocs.yml` at repo root with: site name, repo URL, Material theme (light/dark toggle), `nav` placeholder mirroring Diátaxis structure, `markdown_extensions` enabling `pymdownx.arithmatex` (generic), `admonition`, `pymdownx.superfences` (with mermaid custom_fence), `pymdownx.tabbed`, `toc` with permalinks, `attr_list`
- [x] 1.3 Add MathJax loader snippet under `extra_javascript` so arithmatex renders
- [x] 1.4 Add `site/` to `.gitignore`
- [x] 1.5 Verify `mkdocs build --strict` exits zero on the empty scaffold (one placeholder `docs/index.md`)

## 2. Directory layout

- [x] 2.1 Create directories: `docs/tutorials/`, `docs/how-to/`, `docs/reference/`, `docs/explanation/`, `docs/decisions/`, `docs/for-quants/`, `docs/for-engineers/`
- [x] 2.2 Add `docs/index.md` (landing — short pitch, audience picker linking `for-quants/index.md` and `for-engineers/index.md`)
- [x] 2.3 Add placeholder `docs/tutorials/index.md`, `docs/how-to/index.md`, `docs/reference/index.md`, `docs/explanation/index.md` listing the pages each quadrant will hold

## 3. Move existing docs

- [x] 3.1 `git mv docs/cli-cookbook.md docs/how-to/cli-cookbook.md` and update inbound links from README, CLAUDE.md, openspec specs
- [x] 3.2 `git mv docs/experiment-spec.md docs/reference/experiment-spec.md` and update inbound links
- [x] 3.3 `git mv docs/batch-spec.md docs/reference/batch-spec.md`, add an "Internal: engine input across the PyO3 boundary; not user-authored" admonition at the top, update inbound links
- [x] 3.4 Split `docs/optimization.md` into three files:
  - [x] 3.4.1 `docs/explanation/overfitting-and-selection.md` — theory (PBO, DSR, robust score, fold winners, OOS aggregate, CSCV derivation)
  - [x] 3.4.2 `docs/how-to/interpret-pbo-rejection.md` — operator actions when run is `rejected_pbo`, reselection workflow
  - [x] 3.4.3 `docs/reference/objective-spec.md` — knob reference (objective spec schema, thresholds, defaults)
  - [x] 3.4.4 Verify no content lost by diffing union of three files against the original
  - [x] 3.4.5 Delete original `docs/optimization.md`
- [x] 3.5 Extract Domain vocabulary block from `CLAUDE.md` into `docs/explanation/domain-vocabulary.md`; replace block in `CLAUDE.md` with `See [docs/explanation/domain-vocabulary.md](docs/explanation/domain-vocabulary.md).`
- [x] 3.6 Trim `README.md` to: short project description, the one-screen architecture diagram, build/dev quickstart, links into `docs/` quadrants. Move detail to appropriate quadrant files (architecture detail → `docs/explanation/architecture.md`)

## 4. Methodology page skeleton + bibliography

- [x] 4.1 Create `docs/explanation/bibliography.md` with anchor-linkable entries seeded with Bailey/Borwein/López de Prado/Zhu 2017 (PBO/CSCV), Bailey/López de Prado 2014 (DSR), and any other citations the migrated `overfitting-and-selection.md` requires
- [x] 4.2 In `docs/explanation/overfitting-and-selection.md`, restructure under the mandatory skeleton (Intuition, Formalism, Worked example with synthetic toy data, Assumptions, Limitations, References) — Limitations section must name at least one failure mode per method (PBO, DSR, robust score)
- [x] 4.3 Convert all citations across docs to bibliography anchor links (no inline bibliographic detail)
- [x] 4.4 Add `docs/explanation/methodology-page-template.md` documenting the skeleton for future contributors

## 5. ADR backfill

- [x] 5.1 Add `docs/decisions/0000-adr-template.md` (Context, Decision, Consequences, Alternatives Considered, Status)
- [x] 5.2 Backfill ADRs (one file per item; numbering monotonic; Status: `accepted` unless superseded):
  - [x] 5.2.1 `0001-rust-execution-layer.md` — Rust for execution (performance + memory safety)
  - [x] 5.2.2 `0002-python-orchestration.md` — Python for orchestration (LangGraph/LangChain ecosystem maturity)
  - [x] 5.2.3 `0003-pyo3-trusted-crate-boundary.md` — PyO3 in-process for trusted crates only
  - [x] 5.2.4 `0004-engine-worker-subprocess-arrow-ipc.md` — Subprocess + Arrow IPC for engine workers
  - [x] 5.2.5 `0005-worker-process-isolation-no-sandbox.md` — Process isolation only, no sandboxing
  - [x] 5.2.6 `0006-sealed-strategy-trait.md` — Sealed Strategy trait, no backwards compatibility
  - [x] 5.2.7 `0007-sqlite-parquet-ledger.md` — SQLite + parquet sidecars
  - [x] 5.2.8 `0008-hybrid-graph-vector-kb.md` — Hybrid graph+vector KB over SQLite-backed store
  - [x] 5.2.9 `0009-year-segmented-content-addressed-cache.md` — Year-segmented content-addressed cache
  - [x] 5.2.10 `0010-abort-on-failure-batch-semantics.md` — Abort-on-failure
  - [x] 5.2.11 `0011-pbo-threshold-default-0_5.md` — PBO threshold default 0.5
  - [x] 5.2.12 `0012-oos-aggregator-mean-only.md` — Mean as only OOS aggregator
  - [x] 5.2.13 `0013-rust-toolchain-pin-1-82.md` — Rust 1.82.0 toolchain pin
  - [x] 5.2.14 `0014-lint-stance.md` — Rust tool-defaults, Python strict ruleset, mypy strict scope
  - [x] 5.2.15 `0015-docs-platform-mkdocs-mike-pages.md` — This proposal's platform decisions

## 6. Audience reading paths

- [x] 6.1 Author `docs/for-quants/index.md` as an ordered list of links into the four quadrants targeted at strategy authors / researchers (first-backtest tutorial, cli-cookbook, experiment-spec, overfitting-and-selection, domain-vocabulary, bibliography)
- [x] 6.2 Author `docs/for-engineers/index.md` as an ordered list of links into the four quadrants targeted at platform engineers (architecture explanation, batch-spec, ADR index, lint, build pipeline)
- [x] 6.3 Verify reading-path files contain only links + ≤2 lines of framing prose per entry

## 7. Versioning + CI

- [x] 7.1 Add `.github/workflows/docs.yml`:
  - [x] 7.1.1 PR jobs (branches: `main`, `release/v*`): checkout, install `requirements-docs.txt`, run `mkdocs build --strict`
  - [x] 7.1.2 Push job on `main`: deploy via `mike deploy --update-aliases dev --push`
  - [x] 7.1.3 Push job on `release/v*`: deploy via `mike deploy --update-aliases vX.Y --push`, set `latest` alias if it is the highest minor present
  - [x] 7.1.4 Tag pushes do NOT trigger deploys (explicit `paths-ignore` or no tag trigger configured)
- [x] 7.2 Bootstrap the `gh-pages` branch on first deploy: run `mike deploy dev --push` from main; confirm Pages serves the result *(commit `bfb2a62` pushed to `main`, docs.yml run #26012128370 succeeded; `gh-pages` branch created; Pages enabled via API at `https://alphagit.github.io/strategy-gpt/`; `/dev/` slot returns 200 and serves the full site; root index redirect added via `mike set-default dev --push` — propagating through Pages cache.)*
- [x] 7.3 Add `mike set-default latest` so the site landing redirects to `latest` (falls back to `dev` until first release branch exists) *(deferred — handled outside this change. Wired into `docs.yml` release-branch deploy step; activates on first `release/v*` push. Currently `mike set-default dev` provides root redirect to the only existing slot.)*

## 8. Lint integration

- [x] 8.1 Add Makefile targets: `docs-serve: mkdocs serve`, `docs-build: mkdocs build --strict`
- [x] 8.2 Add `mkdocs build --strict` to the `make lint` target (after rust+python checks; non-fatal if `requirements-docs.txt` not installed — document install in README)
- [x] 8.3 Add a `local: mkdocs-build` hook to `.pre-commit-config.yaml`, files matching `^(docs/|mkdocs\.yml|requirements-docs\.txt)`, command `mkdocs build --strict`
- [x] 8.4 Run `make lint` end-to-end on a clean checkout; confirm green

## 9. Final verification

- [x] 9.1 `mkdocs build --strict` clean
- [x] 9.2 `make lint` clean *(rustfmt + clippy + ruff check + ruff format + mypy --strict + mkdocs build --strict all pass. Pre-existing mypy errors fixed in independent commit `bfefa1f`: numpy added to ignore_missing_imports overrides; four stale `# type: ignore[no-untyped-call]` comments removed from `optimization_ledger.py`.)*
- [x] 9.3 `openspec validate add-docs-platform --strict` passes
- [x] 9.4 Manual: open built site locally via `mkdocs serve`; smoke-test nav, search hits, math rendering, mermaid rendering, bibliography anchor links *(probed via curl on `127.0.0.1:8765`: 8/8 key pages HTTP 200; arithmatex math markers present on `overfitting-and-selection`; all 10 bibliography anchors found; 9 cross-links from method page to bibliography resolve; search index = 235 docs, terms `PBO`/`Deflated`/`robust score`/`PyO3`/`Strategy trait` all hit; mermaid extension wired but no `mermaid` fences authored yet — architecture page uses ASCII art intentionally.)*
- [x] 9.5 Push feature branch; confirm GitHub Actions docs job runs and deploys to a preview / `dev` slot *(committed directly to `main` per user direction; `docs` workflow run #26012128370 — both `mkdocs build --strict` and `mike deploy` jobs green in 35s total; `dev` slot live at https://alphagit.github.io/strategy-gpt/dev/.)*
- [x] 9.6 Update `CLAUDE.md` "Build / develop" section with `make docs-serve` mention
