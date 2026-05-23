"""Tests for multi-line operator input modes in the author dialog."""

from __future__ import annotations

import io
import sys
from collections.abc import Callable, Iterator
from unittest.mock import patch

import pytest

from strategy_gpt.author import read_multiline_reply
from strategy_gpt.cli import paste_aware_input


def _make_ask(lines: list[str]) -> tuple[list[str], Callable[[str], str]]:
    """Build an ``ask_user`` callable that pops from ``lines``; record prompts."""
    captured_prompts: list[str] = []
    it: Iterator[str] = iter(lines)

    def ask(prompt: str) -> str:
        captured_prompts.append(prompt)
        return next(it)

    return captured_prompts, ask


def test_single_line_returns_first_line_verbatim() -> None:
    """Without the ``<<<`` sentinel, the first line is returned as-is."""
    _, ask = _make_ask(["short answer"])
    reply = read_multiline_reply(ask_user=ask, write_user=lambda _: None)
    assert reply == "short answer"


def test_typed_multiline_collects_until_close_sentinel() -> None:
    """``<<<`` opens multi-line, ``>>>`` closes; body joined with newlines."""
    _, ask = _make_ask(["<<<", "first paragraph", "", "second paragraph", ">>>"])
    reply = read_multiline_reply(ask_user=ask, write_user=lambda _: None)
    assert reply == "first paragraph\n\nsecond paragraph"


def test_typed_multiline_announces_mode() -> None:
    """Entering multi-line mode writes a hint to ``write_user``."""
    _, ask = _make_ask(["<<<", "content", ">>>"])
    written: list[str] = []
    read_multiline_reply(ask_user=ask, write_user=written.append)
    assert any("multi-line mode" in line for line in written)


def test_typed_multiline_uses_continuation_prompt() -> None:
    """The continuation prompt for inner lines differs from the first prompt."""
    prompts, ask = _make_ask(["<<<", "a", "b", ">>>"])
    read_multiline_reply(ask_user=ask, write_user=lambda _: None)
    assert prompts[0] == "> "
    # Every subsequent prompt is the continuation marker.
    for prompt in prompts[1:]:
        assert prompt == "... "


def test_paste_aware_input_skipped_when_stdin_not_a_tty() -> None:
    """In CI / piped stdin (the test runner), no probing happens."""
    fake_stdin = io.StringIO("line1\nleftover-shouldnt-be-slurped\n")
    with (
        patch.object(sys, "stdin", fake_stdin),
        patch("builtins.input", side_effect=lambda _prompt: "line1"),
    ):
        out = paste_aware_input("> ")
    assert out == "line1"


def test_paste_aware_input_concatenates_buffered_lines_when_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When stdin is a TTY and additional lines are buffered, slurp them."""
    consumed: list[str] = []

    class _FakeStdin:
        def __init__(self, queued: list[str]) -> None:
            self._queue = list(queued)

        def isatty(self) -> bool:
            return True

        def readline(self) -> str:
            return self._queue.pop(0) if self._queue else ""

    queued = ["second\n", "third\n"]
    fake_stdin = _FakeStdin(queued)

    def fake_select(
        rlist: list[object], _w: object, _x: object, _t: float
    ) -> tuple[list[object], list[object], list[object]]:
        if queued:
            return rlist, [], []
        return [], [], []

    monkeypatch.setattr(sys, "stdin", fake_stdin)
    monkeypatch.setattr("builtins.input", lambda _prompt: "first")
    monkeypatch.setattr("select.select", fake_select)

    out = paste_aware_input("> ")
    del consumed
    assert out == "first\nsecond\nthird"


def test_read_multiline_with_paste_aware_pastes_as_single_first_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pasted block surfaces from ask_user as one already-multi-line string."""
    pasted = "para1 line1\npara1 line2\n\npara2 line1"

    def fake_ask(prompt: str) -> str:
        del prompt
        return pasted

    reply = read_multiline_reply(ask_user=fake_ask, write_user=lambda _: None)
    # Paste doesn't trigger the sentinel branch — full block returned as-is.
    assert reply == pasted
