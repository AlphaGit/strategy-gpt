"""Snapshot tests for the locked-in decisions panel."""

from __future__ import annotations

from strategy_gpt.author_ui import render_decisions_panel


def test_empty_projection_renders_empty_string() -> None:
    """Nothing locked yet ⇒ panel returns ``""`` so the caller can skip."""
    assert render_decisions_panel({}) == ""


def test_mid_dialog_three_fields() -> None:
    """A typical mid-dialog projection renders the canonical labels."""
    projection = {
        "crate_name": "spy-atr",
        "universe": "SPY",
        "mechanism_summary": "ATR-based trend following",
    }
    panel = render_decisions_panel(projection)
    assert "Decisions locked in so far" in panel
    assert "name" in panel
    assert "spy-atr" in panel
    assert "universe" in panel
    assert "SPY" in panel
    assert "mechanism" in panel
    assert "ATR-based trend following" in panel
    min_separators = 3
    assert panel.count("─") >= min_separators


def test_panel_collapses_long_mechanism_summary() -> None:
    """A multi-line mechanism collapses to a single ellipsised line."""
    long_value = "A" * 200
    panel = render_decisions_panel({"crate_name": "x", "mechanism_summary": long_value})
    assert "\n" + "A" * 200 not in panel  # not multi-line
    assert "…" in panel


def test_panel_renders_after_amendment_uses_new_value() -> None:
    """Caller passes the projection; panel reflects whatever's in it."""
    panel = render_decisions_panel({"crate_name": "x", "universe": "SPY,QQQ"})
    assert "SPY,QQQ" in panel
    assert "SPY only" not in panel


def test_panel_renders_smoke_spec_dict() -> None:
    """Dict-valued fields render as ``k=v, k=v`` pairs."""
    panel = render_decisions_panel(
        {
            "crate_name": "x",
            "smoke_spec": {"symbol": "SPY", "start": "2024-01-01"},
        }
    )
    assert "smoke" in panel
    assert "symbol=SPY" in panel
    assert "start=2024-01-01" in panel
