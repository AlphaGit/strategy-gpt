"""Tests for the optimizer rationale generator."""

from __future__ import annotations

from dataclasses import dataclass

from strategy_gpt.hypothesis_loop import KbCitation
from strategy_gpt.optimizer import OptimizerResult, Trial
from strategy_gpt.rationale import (
    RationaleInputs,
    TemplateRationaleClient,
    build_rationale_inputs,
    generate_rationale,
)
from strategy_gpt.types import EvaluationOutcome


def _trial(params: dict[str, object], score: float, accepted: bool = True) -> Trial:
    return Trial(
        params=params,
        metrics={"sharpe": score},
        outcome=EvaluationOutcome(accepted=accepted, score=score, violations=[], soft_misses=[]),
        accepted=accepted,
    )


def test_template_client_rejects_when_no_best() -> None:
    result = OptimizerResult(
        trials=[_trial({"a": 1}, 0.1, accepted=False)],
        best=None,
        rejected_count=1,
    )
    text = generate_rationale(result)
    assert "no accepted parameter set" in text


def test_template_client_renders_best_params_and_features() -> None:
    trials = [_trial({"lookback": i, "threshold": 0.5 + 0.01 * i}, 0.1 * i) for i in range(1, 11)]
    best = max(trials, key=lambda t: t.outcome.score)
    result = OptimizerResult(trials=trials, best=best, rejected_count=0)
    cites = [KbCitation(source="hull-2018", locator="Chapter 4")]
    text = generate_rationale(result, citations=cites)
    assert "Selected parameters" in text
    assert "lookback=10" in text or "lookback=1e+01" in text
    assert "hull-2018" in text


def test_surface_features_detect_correlation() -> None:
    # Strong positive correlation between lookback and score.
    trials = [_trial({"lookback": i}, 0.05 * i) for i in range(1, 13)]
    best = max(trials, key=lambda t: t.outcome.score)
    result = OptimizerResult(trials=trials, best=best, rejected_count=0)
    inputs = build_rationale_inputs(result)
    descs = " ".join(f.description for f in inputs.surface_features)
    assert "lookback" in descs
    assert "correlates" in descs


def test_plateau_detection_when_top_trials_cluster() -> None:
    # Most trials within 5% of the top score.
    trials = [_trial({"x": i}, 1.0 + 0.001 * i) for i in range(20)]
    best = max(trials, key=lambda t: t.outcome.score)
    result = OptimizerResult(trials=trials, best=best, rejected_count=0)
    inputs = build_rationale_inputs(result)
    descs = " ".join(f.description for f in inputs.surface_features)
    assert "plateau" in descs or "broad" in descs


def test_narrow_optimum_when_spread_is_wide() -> None:
    # Spread is wide — only the top trial is within 5%.
    trials = [_trial({"x": i}, 0.1 * i) for i in range(1, 11)]
    best = max(trials, key=lambda t: t.outcome.score)
    result = OptimizerResult(trials=trials, best=best, rejected_count=0)
    inputs = build_rationale_inputs(result)
    descs = " ".join(f.description for f in inputs.surface_features)
    assert "narrow optimum" in descs or "sensitive" in descs


def test_template_client_handles_no_features_gracefully() -> None:
    inputs = RationaleInputs(
        best_params={"a": 1},
        best_score=0.5,
        surface_features=[],
        citations=[],
        accepted_trial_count=1,
        rejected_trial_count=0,
    )
    client = TemplateRationaleClient()
    text = client.write_rationale(inputs)
    assert "Selected parameters" in text
    assert "a=1" in text


@dataclass
class _StubProv:
    source_id: str
    title: str
    author: str | None = None
    year: int | None = None
    section: str | None = None
    page: int | None = None


@dataclass
class _StubKbItem:
    chunk_id: str
    text: str
    score: float
    provenance: _StubProv


@dataclass
class _StubKbResult:
    items: list[_StubKbItem]


class _RecordingKb:
    def __init__(self, items: list[_StubKbItem]) -> None:
        self._items = items
        self.calls: list[tuple[str, int]] = []

    def retrieve(self, query: str, k: int) -> _StubKbResult:
        self.calls.append((query, k))
        return _StubKbResult(items=self._items[:k])


def test_generate_rationale_consults_kb_when_client_supplied() -> None:
    """`param-optimizer::optimized-output-and-rationale` requires the
    rationale generator to consult the KB on top of optimizer output."""
    kb = _RecordingKb(
        items=[
            _StubKbItem(
                chunk_id="c1",
                text="VIX backwardation drives VXX decay.",
                score=0.9,
                provenance=_StubProv(
                    source_id="starter-vol-regimes",
                    title="Vol Regimes",
                    section="Empirical Properties",
                ),
            )
        ]
    )
    trials = [_trial({"vol_lo": 0.01 * i, "vol_hi": 0.04 + 0.01 * i}, 0.1 * i) for i in range(1, 6)]
    best = max(trials, key=lambda t: t.outcome.score)
    result = OptimizerResult(trials=trials, best=best, rejected_count=0)
    text = generate_rationale(
        result,
        kb_client=kb,
        strategy_name="vxx_volatility_range",
        kb_top_k=3,
    )
    assert len(kb.calls) == 1
    query, k = kb.calls[0]
    assert "vxx_volatility_range" in query
    assert "vol_lo" in query
    assert "vol_hi" in query
    assert k == 3
    assert "starter-vol-regimes" in text


def test_generate_rationale_dedups_kb_and_caller_citations() -> None:
    """Same (source, locator) pair from caller + KB should collapse to one."""
    kb = _RecordingKb(
        items=[
            _StubKbItem(
                chunk_id="c1",
                text="dup",
                score=0.5,
                provenance=_StubProv(source_id="s1", title="T", section="Ch.1"),
            )
        ]
    )
    trials = [_trial({"x": i}, 0.1 * i) for i in range(1, 5)]
    best = max(trials, key=lambda t: t.outcome.score)
    result = OptimizerResult(trials=trials, best=best, rejected_count=0)
    caller_cite = KbCitation(source="s1", locator="Ch.1")
    text = generate_rationale(result, citations=[caller_cite], kb_client=kb)
    assert text.count("[s1: Ch.1]") == 1


def test_generate_rationale_skips_kb_call_when_no_best() -> None:
    """No best trial → no query to compose, so no KB call is made."""

    class _StubKb:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int]] = []

        def retrieve(self, query: str, k: int) -> object:
            self.calls.append((query, k))
            raise AssertionError("retrieve must not be invoked when best is None")

    result = OptimizerResult(
        trials=[_trial({"a": 1}, 0.0, accepted=False)],
        best=None,
        rejected_count=1,
    )
    kb = _StubKb()
    # No strategy_name → query becomes empty → retrieval skipped.
    text = generate_rationale(result, kb_client=kb)
    assert kb.calls == []
    assert "no accepted parameter set" in text


def test_generate_rationale_uses_supplied_client() -> None:
    class StubClient:
        def __init__(self) -> None:
            self.last_inputs: RationaleInputs | None = None

        def write_rationale(self, inputs: RationaleInputs) -> str:
            self.last_inputs = inputs
            return "STUB"

    trials = [_trial({"a": i}, 0.1 * i) for i in range(1, 6)]
    best = max(trials, key=lambda t: t.outcome.score)
    result = OptimizerResult(trials=trials, best=best, rejected_count=0)
    client = StubClient()
    text = generate_rationale(result, client=client)
    assert text == "STUB"
    assert client.last_inputs is not None
    assert client.last_inputs.best_params == best.params
