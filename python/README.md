# strategy-gpt (Python orchestrator)

Python side of the strategy-gpt rewrite. Hosts the LangGraph hypothesis loop, the parameter optimizer, the tester, and thin clients for the Rust core (data gateway, engine, ledger, knowledge base) exposed through `strategy_gpt._native` via PyO3/maturin.

## Develop

```bash
# from this directory
pip install -e '.[dev]'
maturin develop -m ../crates/py-bindings/Cargo.toml
```

## Lint and pre-commit

The repository's lint suite is driven from the root. From the repo root:

```bash
pre-commit install   # one-time
make lint            # ruff check + ruff format --check + mypy --strict
make fmt             # ruff format (writes changes)
```

Strict Python: ruff rule set covers correctness, style, security, and complexity. `mypy --strict` runs over `python/strategy_gpt/`. See `python/pyproject.toml` for the full configuration.

See top-level `CLAUDE.md` for full layout and contracts.
