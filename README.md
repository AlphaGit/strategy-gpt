# strategy-gpt

LLM-driven research loop for **creating and testing** quantitative trading strategies. Polyglot: Rust core (engine, data gateway, ledger, knowledge base) + Python orchestrator (LangGraph hypothesis loop, optimizer, tester).

This is a research platform, not a trading platform. The engine simulates fills inside a backtest only to evaluate hypotheses; there is no live trading, broker integration, or real-time position management.

> The architecture rewrite specified in `openspec/changes/rewrite-architecture/` is feature-complete at v0.1.0. The pre-rewrite reference implementation is preserved at the `pre-rewrite` git tag. See `CLAUDE.md` for the durable architecture and contracts.

## Workflow at a glance

```
data fetch → engine batch → ledger record → KB-aware hypothesis loop → tester → engine → verdict
```

- **data-gateway** fetches, caches, normalizes, and consolidates multi-provider bars.
- **engine** runs batched backtests with native stress + sensitivity modes.
- **ledger** records every run/hypothesis/decision append-only with parquet sidecars.
- **kb** retrieves citations from a curated corpus (graph + vector hybrid).
- **hypothesis-loop** diagnoses results, queries KB, generates + critiques candidates.
- **tester** translates a hypothesis into a strategy diff or new Rust source, smoke-tests, then submits a full batch.
- **optimizer** runs grid / random / TPE search over walk-forward folds, with an LLM-grounded rationale.

The reference VXX volatility-range strategy lives in `crates/vxx-strategy/`; its objective spec is `crates/vxx-strategy/objective.yaml`. Run the recorded smoke pass with:

```bash
cd python && python -c "from strategy_gpt.smoke import run_smoke; print(run_smoke())"
```

## Repo layout

```
crates/                 Rust workspace
  engine-rt/            Strategy trait, Context capability handle, RunnerVersion
  engine/               BatchSpec, coordinator, worker, modes
  data-gateway/         providers, cache, normalizer, consolidator
  ledger/               SQLite append-only + parquet sidecars
  kb/                   Kuzu (graph) + LanceDB (vector) hybrid retrieval
  build-pipeline/       lint, allowed-crate enforcement, cargo build
  py-bindings/          PyO3 module exposing trusted crates as `strategy_gpt._native`
  vxx-strategy/         Reference VXX volatility-range smoke strategy cdylib
  example-strategy/     No-op fixture used by plugin-loader tests
python/strategy_gpt/    Orchestrator (LangGraph workflows, optimizer, tester, CLI)
kb/                     Curated source list, starter corpus, and recorded fixtures
openspec/               Change proposals and capability specs
```

## Develop

```bash
# 1. Rust workspace
cd crates && cargo check --workspace

# 2. Python orchestrator + native extension
cd ../python && pip install -e '.[dev]'
pip install maturin
maturin develop -m ../crates/py-bindings/Cargo.toml

# 3. Build the reference strategy + worker so end-to-end tests can load them
cd ../crates && cargo build -p vxx-strategy -p example-strategy --bin engine-worker
```

`ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY` are required only when running the hypothesis loop with a real reasoning model. The smoke run (`python -m strategy_gpt.smoke`, the recorded fixture under `kb/fixtures/smoke_run.json`, and the offline tests) use stubbed reasoning so they run without credentials.

## Lint and test

```bash
pre-commit install   # one-time
make lint            # rustfmt + clippy + ruff + mypy
make fmt             # apply formatters
make test            # cargo test --workspace + pytest
```

CI runs the same `make lint` + `make test` plus a smoke-fixture byte-identity check; see `.github/workflows/ci.yml`. The full lint stance (Rust defaults, strict Python) is documented in `CLAUDE.md`.

## OpenSpec

Active changes:

- `rewrite-architecture` — full rewrite of the system into the polyglot scaffold above.
- `add-lint-precommit` — this lint and pre-commit baseline.

```bash
openspec list
openspec status --change "<name>"
```

## License

MIT OR Apache-2.0.
