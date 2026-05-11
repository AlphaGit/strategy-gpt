"""Structured logging for the Python orchestrator.

Configures :mod:`structlog` to emit JSON or developer-friendly KV
records and binds a per-run correlation id so every log line emitted
during a batch (Python + Rust worker) can be joined on ``run_id``. The
Rust side has its own :mod:`tracing`-based initializer; cross-process
correlation works because the orchestrator sets
``STRATEGY_GPT_RUN_ID`` before spawning the worker, and the worker
reads it as a span field.

Default output is JSON: research workflows are long-running and the
orchestrator's logs are the canonical artifact for post-hoc analysis,
so structured records that survive grep/jq are worth more than pretty
ANSI output. ``format="pretty"`` switches to console rendering for
interactive debugging.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Final, Literal

import structlog

LogFormat = Literal["json", "pretty"]

_DEFAULT_LEVEL: Final[str] = "info"
_RUN_ID_ENV: Final[str] = "STRATEGY_GPT_RUN_ID"


def _level_from_name(name: str) -> int:
    level = logging.getLevelNamesMapping().get(name.upper())
    if level is None:
        msg = f"unknown log level: {name!r}"
        raise ValueError(msg)
    return level


def configure_logging(
    *,
    format: LogFormat = "json",
    level: str = _DEFAULT_LEVEL,
) -> None:
    """Configure :mod:`structlog` and stdlib :mod:`logging` for the
    orchestrator process.

    Idempotent: callers may invoke this multiple times (e.g., once from
    the CLI entry point and once from a Jupyter session) without
    duplicating handlers. The function reconfigures the stdlib root
    logger so libraries logging through :mod:`logging` also flow into
    the structured pipeline.
    """
    log_level = _level_from_name(level)
    renderer: structlog.types.Processor
    if format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Replace any existing handlers so repeat calls don't duplicate output.
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setLevel(log_level)
    root.addHandler(handler)
    root.setLevel(log_level)


def new_run_id() -> str:
    """Mint a fresh run id (uuid4 hex). The orchestrator calls this once
    per batch submission; the same id flows into the ledger row, the
    structlog contextvar, and the worker subprocess env."""
    return uuid.uuid4().hex


@contextmanager
def bind_run_id(
    run_id: str,
    *,
    env: dict[str, str] | None = None,
) -> Iterator[str]:
    """Bind ``run_id`` to every structlog record inside the ``with`` block
    and export it as ``STRATEGY_GPT_RUN_ID`` so subprocesses inherit it.

    `env` defaults to :data:`os.environ`; tests may supply a stand-in to
    inspect the side-effect without leaking into the process state. On
    exit the contextvar is unbound and the env variable is restored to
    its prior value (or removed if it was unset).
    """
    target_env = env if env is not None else os.environ
    previous: str | None = target_env.get(_RUN_ID_ENV)
    target_env[_RUN_ID_ENV] = run_id
    token = structlog.contextvars.bind_contextvars(run_id=run_id)
    try:
        yield run_id
    finally:
        structlog.contextvars.reset_contextvars(**token)
        if previous is None:
            target_env.pop(_RUN_ID_ENV, None)
        else:
            target_env[_RUN_ID_ENV] = previous


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Wrapper over :func:`structlog.get_logger` that returns a typed
    logger. Use module ``__name__`` as ``name`` so the field shows up in
    the structured record."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger


__all__ = [
    "LogFormat",
    "bind_run_id",
    "configure_logging",
    "get_logger",
    "new_run_id",
]
