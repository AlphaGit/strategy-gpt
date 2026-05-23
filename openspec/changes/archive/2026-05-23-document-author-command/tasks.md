## 1. New author tutorial

- [x] 1.1 Create `docs/tutorials/author-a-strategy.md` with the canonical tutorial skeleton (`Learning goal`, `Prerequisites`, `Walkthrough`, `What you just did`, `What next`).
- [x] 1.2 Walkthrough section: number the steps and cover, in order, (a) installing the orchestrator and exporting an API key, (b) invoking `strategy-gpt author "..."` with a concrete frozen seed, (c) walking through 2-3 illustrative clarifying-question turns with example operator answers, (d) showing the four on-disk files written under `crates/<name>-strategy/` with brief excerpts, (e) reading back `intent.toml` via `strategy_gpt.author.load_intent_toml`, and (f) optionally chaining into `strategy-gpt hypothesize`.
- [x] 1.3 Add an explicit prose note in the Walkthrough that the dialog wording is non-load-bearing and that the LLM's clarifying questions will vary across runs.
- [x] 1.4 `What next` section: cross-link the how-to (`../how-to/author-a-strategy.md`), the engineer-targeted explanation (`../explanation/hand-authoring-a-strategy.md`), the relevant ADR / engine-rt PROMPT_API, and the hypothesize tutorial.

## 2. Relocate hand-authored guide

- [x] 2.1 Move `docs/tutorials/authoring-a-strategy.md` to `docs/explanation/hand-authoring-a-strategy.md`. Use `git mv` so file history is preserved.
- [x] 2.2 Rewrite the page intro (top of file, above the existing `## Learning goal`) so it positions itself as engineer-targeted reference: "This page is the deep dive on the trait surface the LLM is targeting when it emits a strategy. Read it when extending the trait surface, debugging an author emission, or contributing to the engine. For the default creation path, use `strategy-gpt author`." The existing walkthrough body stays verbatim.
- [x] 2.3 Rename the page's H1 from `# Authoring a strategy` to `# Hand-authoring a strategy` so it does not clash with the tutorial title in search.
- [x] 2.4 Update the page's `## What next` section to reference the author tutorial as the recommended creation path.

## 3. mkdocs.yml nav restructure

- [x] 3.1 In `mkdocs.yml`, replace the Tutorials entry `Authoring a strategy: tutorials/authoring-a-strategy.md` with `Author a strategy: tutorials/author-a-strategy.md`.
- [x] 3.2 Reorder Tutorials so `Your first backtest` is first and `Author a strategy` is second; preserve the existing positions of `Walking the hypothesize loop` and `Running an optimization`.
- [x] 3.3 Add `Hand-authoring a strategy: explanation/hand-authoring-a-strategy.md` to the Explanation section, placed after `Domain vocabulary` and before `Architecture`.

## 4. CLI cookbook coverage

- [x] 4.1 In `docs/how-to/cli-cookbook.md`, add a top-level `## Author a strategy` section, placed after the existing strategies block and before the hypothesis-loop / optimization sections (or in the closest equivalent position if the document has been reorganized).
- [x] 4.2 Subsections: (a) "Invoke with a seed" — the basic happy-path bash example, (b) "Edit an existing crate" — re-running author against an existing name and answering `edit`, (c) "Verify against the full batch" — `--verify=batch` use, (d) "Tune the repair budget" — `--k-repair-emit` / `--k-repair-build` flag table, (e) "Troubleshooting" — bullet list covering budget-exhaustion (return to dialog), non-whitelisted dep (whitelist file pointer), and smoke-fail-no-trades.
- [x] 4.3 Add cross-links at the bottom of the section to `../tutorials/author-a-strategy.md` and `./author-a-strategy.md`.

## 5. Cross-reference sweep

- [x] 5.1 Update `docs/tutorials/index.md` to list the new `Author a strategy` tutorial under the same heading the other tutorial entries occupy, in the same order they appear in `mkdocs.yml`.
- [x] 5.2 Update `docs/how-to/index.md` to list the existing `author-a-strategy.md` how-to under the same heading the other how-to entries occupy.
- [x] 5.3 Update the existing `docs/how-to/author-a-strategy.md` cross-reference that points at `../tutorials/authoring-a-strategy.md` so it points at `../explanation/hand-authoring-a-strategy.md`.
- [x] 5.4 Grep the docs tree (`docs/`, `README.md`, `CLAUDE.md`, `openspec/specs/`) for any other link to `tutorials/authoring-a-strategy.md` and rewrite to the new explanation path.
- [x] 5.5 Run `grep -r "authoring-a-strategy" docs README.md CLAUDE.md 2>/dev/null` and confirm no remaining matches outside the new explanation page (where the slug "hand-authoring-a-strategy" naturally appears).

## 6. Verification

- [x] 6.1 Run `mkdocs build --strict` from the repo root; confirm zero broken links and zero strict-mode warnings.
- [x] 6.2 Run `make lint` and confirm CI gates stay green.
- [x] 6.3 Run `openspec validate document-author-command --strict` and resolve any spec-format issues.
- [x] 6.4 Visually inspect the rendered site by running `make docs-serve` and walking the new tutorial, the relocated explanation page, the cookbook's Author section, and both index pages.
