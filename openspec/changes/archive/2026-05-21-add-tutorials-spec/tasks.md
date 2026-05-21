## 1. Author tutorials

- [x] 1.1 Author `docs/tutorials/first-backtest.md` against the skeleton (Learning goal, Prerequisites, Walkthrough with command/output pairs, What you just did, What next). Walkthrough uses `crates/vxx-strategy/` end to end: clone → `pip install -e python/[dev]` → `maturin develop` → `strategy-gpt run --spec experiments/vxx.yaml` → read `BacktestResult` output.
- [x] 1.2 Author `docs/tutorials/authoring-a-strategy.md` against the skeleton. Walkthrough copies `crates/example-strategy/` as a starting point, walks through the `Strategy` trait surface from `crates/engine-rt/PROMPT_API.md` (lifecycle methods, `Context` capabilities, `params_schema.json`), builds via `cargo build -p <new-strategy>`, and verifies with `strategy-gpt run`.
- [x] 1.3 Author `docs/tutorials/running-an-optimization.md` against the skeleton. Walkthrough authors an experiment spec for the VXX strategy, runs `strategy-gpt optimize --spec experiments/vxx-opt.yaml`, inspects `best.json` and `manifest.json`, runs `strategy-gpt optimize compare`.

## 2. Replace the placeholder index

- [x] 2.1 Replace `docs/tutorials/index.md` with a clean index that links to the three committed pages (no TODOs, no "coming soon" entries). Each link entry includes one-line framing prose ≤ 2 lines per the existing reading-path convention.

## 3. Wire mkdocs nav

- [x] 3.1 Add nav entries for the three tutorials under the Tutorials section of `mkdocs.yml`, mirroring the filesystem path order from the new index.

## 4. Verify

- [x] 4.1 `mkdocs build --strict` exits clean against the new pages.
- [x] 4.2 `make lint` clean (no new lint surface, but verify the docs-build step still passes).
- [x] 4.3 Each tutorial walkthrough is reproducible from a fresh checkout by running its commands in order (manual smoke; the reviewer who lands the page runs the walkthrough end to end).
- [x] 4.4 Tutorials index page lists exactly the three new pages and resolves all links under `mkdocs build --strict`.
