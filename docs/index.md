# strategy-gpt

LLM-driven research loop for **creating and testing** quantitative trading strategies. Polyglot: a Rust core (engine, data gateway, ledger, knowledge base, build pipeline) wrapped by a Python orchestrator (LangGraph hypothesis loop, optimizer, tester).

!!! info "Research platform, not a trading platform"
    The engine simulates fills against historical bars purely to evaluate hypotheses. No live trading, no broker integration, no real-time position management.

The product is the *loop*: `hypothesis → code → backtest → verdict → next hypothesis`.

## Start here

**[Guided CLI walkthrough](guided-cli-walkthrough.md)** — the recommended entry point for operators. Nine stages tracing the natural usage arc from setup through reproducibility, each linking out to the depth pages below.

## Pick your reading path

- **[For quants](for-quants/index.md)** — strategy authors and researchers operating the system, reading results, reasoning about methodology.
- **[For engineers](for-engineers/index.md)** — platform engineers extending the system: trust boundaries, module contracts, build pipeline.

## Or browse by intent

- **[Tutorials](tutorials/index.md)** — learn by doing.
- **[How-to](how-to/index.md)** — task recipes for specific goals.
- **[Reference](reference/index.md)** — schemas, knobs, CLI surface.
- **[Explanation](explanation/index.md)** — methodology, architecture, vocabulary.
- **[Decisions](decisions/index.md)** — Architecture Decision Records.
