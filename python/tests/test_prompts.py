"""Tests for the stage-1/2/3 prompt builders."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from strategy_gpt.diagnose import (
    Diagnosis,
    RegimePerformance,
    SignalMisfire,
    TradeStats,
)
from strategy_gpt.hypothesis_loop import (
    KbCitation,
    PriorDecision,
)
from strategy_gpt.markdown_io import Stage1Idea, Stage2Commitments
from strategy_gpt.prompts import (
    build_stage1_prompt,
    build_stage2_prompt,
    build_stage3_prompt,
)
from strategy_gpt.types import (
    BacktestMetrics,
    DecisionKind,
    HypothesisRecord,
)


def _diag() -> Diagnosis:
    return Diagnosis(
        metrics=BacktestMetrics(
            sharpe=1.42,
            sortino=2.10,
            profit_factor=1.30,
            win_ratio=0.55,
            max_drawdown=-0.12,
            annualized_return=0.18,
            n_trades=42,
            avg_trade_length_bars=8.0,
        ),
        trade_stats=TradeStats(
            n_total=42,
            n_winners=23,
            n_losers=19,
            n_breakeven=0,
            win_rate=0.55,
            avg_pnl=0.005,
            avg_winner_pnl=0.015,
            avg_loser_pnl=-0.008,
            largest_winner_pnl=0.04,
            largest_loser_pnl=-0.02,
            total_pnl=0.21,
            total_fees=0.005,
            avg_trade_length_seconds=900.0,
            long_count=20,
            short_count=22,
        ),
        regime_performance=[
            RegimePerformance(
                label="high_vol",
                n_trades=10,
                total_pnl=-0.05,
                win_rate=0.3,
                avg_pnl=-0.005,
                coverage_bars=40,
            ),
        ],
        signal_misfires=[
            SignalMisfire(
                signal="rsi_oversold",
                fired_count=12,
                suppressed_count=3,
                used_count=8,
                fired_no_trade_count=4,
                suppression_reasons={},
            ),
        ],
        exec_log_summary={"entry_skipped": 5},
    )


def _cite(source: str, locator: str) -> KbCitation:
    return KbCitation(source=source, locator=locator, excerpt="Volatility clustering implies ...")


def _prior_record(name: str) -> HypothesisRecord:
    return HypothesisRecord(
        id=name,
        name=name,
        target_metric="sharpe",
        falsification={},
        proposed_change={},
        kb_cites=[],
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _prior(name: str, kind: DecisionKind) -> PriorDecision:
    return PriorDecision(
        decision_id=f"dec_{name}",
        kind=kind,
        rationale=f"rationale for {name}",
        evidence=None,
        decided_at=datetime(2026, 1, 1, tzinfo=UTC),
        hypothesis=_prior_record(name),
    )


# ---------------- Stage 1 ----------------


def test_stage1_prompt_contains_required_sections() -> None:
    prompt = build_stage1_prompt(
        strategy_name="vxx_vol_range",
        diagnosis=_diag(),
        kb_cites=[_cite("book.pdf", "p.42")],
        prior_decisions=[_prior("hedge_leg", DecisionKind.ACCEPTED)],
        intra_run_history=[],
    )
    assert "Idea" in prompt.system  # system mentions emission contract
    assert "vxx_vol_range" in prompt.user
    assert "Diagnosis" in prompt.user
    assert "Knowledge-base citations" in prompt.user
    assert "book.pdf" in prompt.user
    assert "hedge_leg" in prompt.user


def test_stage1_prompt_handles_empty_inputs() -> None:
    prompt = build_stage1_prompt(
        strategy_name="empty",
        diagnosis=_diag(),
        kb_cites=[],
        prior_decisions=[],
    )
    assert "(none)" in prompt.user


def test_stage1_prompt_truncates_long_excerpt() -> None:
    long_excerpt = "x" * 1000
    long = KbCitation(source="big.pdf", locator="p.1", excerpt=long_excerpt)
    prompt = build_stage1_prompt(
        strategy_name="s",
        diagnosis=_diag(),
        kb_cites=[long],
        prior_decisions=[],
    )
    # Excerpt is truncated to 240 chars in the prompt body.
    assert prompt.user.count("x") < 300


# ---------------- Stage 2 ----------------


def test_stage2_prompt_embeds_locked_stage1_verbatim() -> None:
    stage1_text = "# Idea\n\ncandidate_name: foo\nrationale: bar\n"
    parsed = Stage1Idea(
        candidate_name="foo",
        rationale="bar",
        expected_lift_confidence=0.3,
        expected_side_effects=["a"],
    )
    prompt = build_stage2_prompt(
        strategy_name="s",
        stage1_response=stage1_text,
        stage1_parsed=parsed,
        prompt_api="# engine-rt PROMPT_API\nminimal\n",
        baseline_params_schema={"schema_version": 1, "params": []},
        allowed_metrics=["sharpe", "max_drawdown"],
    )
    assert stage1_text.strip() in prompt.user
    assert "Falsification" in prompt.system
    assert "ParamIntent" in prompt.system
    assert "schema_version" in prompt.user
    assert "sharpe" in prompt.user
    assert "max_drawdown" in prompt.user
    assert "PROMPT_API" in prompt.user


def test_stage2_prompt_with_no_baseline_schema() -> None:
    prompt = build_stage2_prompt(
        strategy_name="s",
        stage1_response="# Idea\n",
        stage1_parsed=Stage1Idea(
            candidate_name="x",
            rationale="y",
            expected_lift_confidence=0.5,
            expected_side_effects=[],
        ),
        prompt_api="",
        baseline_params_schema=None,
        allowed_metrics=[],
    )
    assert "no declared params" in prompt.user
    assert "(unrestricted)" in prompt.user


# ---------------- Stage 3 ----------------


def test_stage3_prompt_locks_both_prior_stages() -> None:
    stage1 = "# Idea\n\ncandidate_name: x\n"
    stage2 = "# Falsification\n\n```yaml\nprimary: {}\n```\n"
    commitments = Stage2Commitments(
        falsification={"primary": {"metric": "sharpe", "direction": "gt"}},
        param_intent={"added": [], "kept": [], "removed": []},
    )
    files = {"Cargo.toml": "[package]\nname='x'\n", "src/lib.rs": "fn main(){}\n"}
    prompt = build_stage3_prompt(
        strategy_name="s",
        stage1_response=stage1,
        stage2_response=stage2,
        stage2_parsed=commitments,
        prompt_api="API\n",
        baseline_files=files,
    )
    assert stage1.strip() in prompt.user
    assert stage2.strip() in prompt.user
    assert "Cargo.toml" in prompt.user
    assert "src/lib.rs" in prompt.user
    assert "DELETE" in prompt.system


def test_stage3_prompt_truncates_large_baseline_files() -> None:
    huge = "x" * 20000
    files = {"src/lib.rs": huge}
    prompt = build_stage3_prompt(
        strategy_name="s",
        stage1_response="",
        stage2_response="",
        stage2_parsed=Stage2Commitments(falsification={}, param_intent={}),
        prompt_api="",
        baseline_files=files,
    )
    assert "<truncated>" in prompt.user
    # The truncation cap is 8 KiB per file plus prompt overhead.
    assert len(prompt.user) < 20000


def test_stage3_prompt_embeds_engine_rt_source_when_dir_supplied(tmp_path: Path) -> None:
    """Stage-3 prompt MUST include verbatim contents of every ``.rs``
    file under the supplied ``engine_rt_src_dir`` so the LLM cannot
    hallucinate methods that do not exist on the ``Context`` trait.
    """
    src = tmp_path / "engine-rt-src"
    src.mkdir()
    (src / "context.rs").write_text(
        "pub trait Context {\n    fn submit_order(&mut self, side: Side, size: f64);\n}\n"
    )
    (src / "strategy.rs").write_text("pub trait Strategy {}\n")

    prompt = build_stage3_prompt(
        strategy_name="s",
        stage1_response="",
        stage2_response="",
        stage2_parsed=Stage2Commitments(falsification={}, param_intent={}),
        prompt_api="API",
        baseline_files={},
        engine_rt_src_dir=src,
    )
    assert "engine-rt source" in prompt.user
    assert "context.rs" in prompt.user
    assert "submit_order" in prompt.user
    assert "strategy.rs" in prompt.user


def test_stage3_prompt_omits_engine_rt_section_when_dir_unset() -> None:
    prompt = build_stage3_prompt(
        strategy_name="s",
        stage1_response="",
        stage2_response="",
        stage2_parsed=Stage2Commitments(falsification={}, param_intent={}),
        prompt_api="API",
        baseline_files={},
    )
    assert "engine-rt source" not in prompt.user


def test_stage3_prompt_handles_empty_baseline() -> None:
    prompt = build_stage3_prompt(
        strategy_name="s",
        stage1_response="",
        stage2_response="",
        stage2_parsed=Stage2Commitments(falsification={}, param_intent={}),
        prompt_api="",
        baseline_files={},
    )
    assert "no source files" in prompt.user


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
