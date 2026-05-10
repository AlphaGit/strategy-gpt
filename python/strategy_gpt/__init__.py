"""Strategy-GPT — LLM-driven research loop for quantitative trading strategies.

Top-level Python orchestrator. The Rust core is exposed through
``strategy_gpt._native``; thin wrappers under this package provide a
typed Python API for the orchestrator (LangGraph workflows, optimizer,
tester, ledger client).
"""

from importlib import metadata as _metadata

try:
    __version__ = _metadata.version("strategy-gpt")
except _metadata.PackageNotFoundError:
    __version__ = "0.1.0"
