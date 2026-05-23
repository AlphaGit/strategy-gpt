# Tutorials

Learn-by-doing walkthroughs. Each tutorial follows a single happy path end to end, with command and expected output paired at every step.

- [Your first backtest](first-backtest.md) — run the bundled VXX reference strategy from a fresh clone and read the resulting `BacktestResult`.
- [Author a strategy](author-a-strategy.md) — drive `strategy-gpt author` end-to-end from a natural-language seed; watch the LLM emit, build, and smoke-test a new `cdylib`.
- [Walking the hypothesize loop](hypothesize-loop.md) — generate a fixture per-strategy ledger and exercise the `hypothesize`, `hypothesis replay`, and `hypothesis diff` commands without LLM API keys.
- [Running an optimization](running-an-optimization.md) — sweep parameters across cross-validation folds, inspect `best.json`, and re-rank a trial set under a different objective.
