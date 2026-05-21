# Tutorials

Learn-by-doing walkthroughs. Each tutorial follows a single happy path end to end, with command and expected output paired at every step.

- [Your first backtest](first-backtest.md) — run the bundled VXX reference strategy from a fresh clone and read the resulting `BacktestResult`.
- [Authoring a strategy](authoring-a-strategy.md) — copy `example-strategy`, implement the sealed `Strategy` trait, and run your own `cdylib` through the engine.
- [Running an optimization](running-an-optimization.md) — sweep parameters across cross-validation folds, inspect `best.json`, and re-rank a trial set under a different objective.
