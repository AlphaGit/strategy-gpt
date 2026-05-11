"""Structured-logging configuration tests."""

from __future__ import annotations

import json
import logging
from io import StringIO

import pytest
import structlog

from strategy_gpt.logging_config import (
    bind_run_id,
    configure_logging,
    get_logger,
    new_run_id,
)


@pytest.fixture(autouse=True)
def _reset_structlog() -> None:
    structlog.reset_defaults()


def test_new_run_id_is_hex_uuid_and_unique() -> None:
    a = new_run_id()
    b = new_run_id()
    assert len(a) == 32
    assert a != b
    int(a, 16)  # hex round-trip; raises if not hex


def test_configure_logging_emits_json_records() -> None:
    buffer = StringIO()
    configure_logging(format="json", level="info")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=buffer),
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=False,
    )
    logger = get_logger("test")
    logger.info("smoke", run_id="abc", trial=1)
    payload = json.loads(buffer.getvalue().strip())
    assert payload["event"] == "smoke"
    assert payload["run_id"] == "abc"
    assert payload["trial"] == 1
    assert payload["level"] == "info"


def test_bind_run_id_propagates_to_log_events() -> None:
    buffer = StringIO()
    configure_logging(format="json", level="info")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=buffer),
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=False,
    )
    logger = get_logger("test")
    with bind_run_id("run-123", env={}):
        logger.info("inside")
    logger.info("outside")
    lines = [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]
    inside = next(rec for rec in lines if rec["event"] == "inside")
    outside = next(rec for rec in lines if rec["event"] == "outside")
    assert inside["run_id"] == "run-123"
    assert "run_id" not in outside


def test_bind_run_id_sets_env_for_subprocesses() -> None:
    env: dict[str, str] = {}
    with bind_run_id("run-xyz", env=env):
        assert env["STRATEGY_GPT_RUN_ID"] == "run-xyz"
    assert "STRATEGY_GPT_RUN_ID" not in env


def test_bind_run_id_restores_previous_env_on_exit() -> None:
    env: dict[str, str] = {"STRATEGY_GPT_RUN_ID": "prior"}
    with bind_run_id("inner", env=env):
        assert env["STRATEGY_GPT_RUN_ID"] == "inner"
    assert env["STRATEGY_GPT_RUN_ID"] == "prior"


def test_configure_logging_rejects_bad_level() -> None:
    with pytest.raises(ValueError, match="unknown log level"):
        configure_logging(level="loud")


def test_configure_logging_replaces_existing_handlers() -> None:
    configure_logging(format="pretty", level="info")
    first_handlers = list(logging.getLogger().handlers)
    configure_logging(format="json", level="warning")
    second_handlers = list(logging.getLogger().handlers)
    # Same count, no leak — and the prior handler was replaced rather
    # than supplemented.
    assert len(second_handlers) == 1
    assert second_handlers[0] is not first_handlers[0]
    assert logging.getLogger().level == logging.WARNING
