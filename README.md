# strategy-gpt

LLM-driven research loop for **creating and testing** quantitative trading strategies. Polyglot: Rust core (engine, data gateway, ledger, knowledge base) + Python orchestrator (LangGraph hypothesis loop, optimizer, tester).

This is a research platform, not a trading platform. The engine simulates fills inside a backtest only to evaluate hypotheses; there is no live trading, broker integration, or real-time position management.

> Currently undergoing a full rewrite. The pre-rewrite reference implementation is preserved at the `pre-rewrite` git tag. See `openspec/changes/rewrite-architecture/` for the current scope and `CLAUDE.md` for the durable architecture and contracts.

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
python/strategy_gpt/    Orchestrator (LangGraph workflows, optimizer, tester, CLI)
kb/                     Curated source list and ingestion scripts
openspec/               Change proposals and capability specs
```

## Develop

```bash
# Rust workspace
cd crates && cargo check --workspace

# Python orchestrator
cd python && pip install -e '.[dev]'
maturin develop -m ../crates/py-bindings/Cargo.toml
```

## Lint and pre-commit

```bash
pre-commit install   # one-time
make lint            # full suite (Rust + Python)
make fmt             # apply formatters
make test            # cargo test + (later) pytest
```

The same `make lint` runs in CI when it lands. See `CLAUDE.md` for the full lint stance (Rust defaults, strict Python).

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
