# strategy-gpt (Python orchestrator)

Python side of the strategy-gpt rewrite. Hosts the LangGraph hypothesis loop, the parameter optimizer, the tester, and thin clients for the Rust core (data gateway, engine, ledger, knowledge base) exposed through `strategy_gpt._native` via PyO3/maturin.

## Develop

```bash
# from this directory
pip install -e '.[dev]'
maturin develop -m ../crates/py-bindings/Cargo.toml
```

See top-level `CLAUDE.md` for full layout and contracts.
