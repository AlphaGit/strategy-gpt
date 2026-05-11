"""Tester translation tests — parameter-only fast path (task 10.1)."""

from __future__ import annotations

import pytest

from strategy_gpt.hypothesis_loop import HypothesisCandidate
from strategy_gpt.tester import (
    ParamDiff,
    ParamOnlyTranslationError,
    apply_param_diffs,
    parse_param_only_change,
    translate_param_only,
)


def _candidate(proposed_change: object) -> HypothesisCandidate:
    return HypothesisCandidate(
        name="lower_vol_lo",
        target_metric="sharpe",
        falsification={"op": ">=", "value": 1.5},
        proposed_change=proposed_change,
        estimated_lift_confidence=0.5,
    )


def test_parse_single_param_diff() -> None:
    diffs = parse_param_only_change({"param": "vol_lo", "from": 10, "to": 5})
    assert diffs == [ParamDiff(param="vol_lo", from_value=10, to_value=5)]


def test_parse_bulk_diffs_preserves_order() -> None:
    diffs = parse_param_only_change(
        {
            "diffs": [
                {"param": "vol_lo", "from": 10, "to": 5},
                {"param": "vol_hi", "from": 30, "to": 25},
            ]
        }
    )
    assert [d.param for d in diffs] == ["vol_lo", "vol_hi"]
    assert diffs[1].to_value == 25


def test_parse_missing_to_raises() -> None:
    with pytest.raises(ParamOnlyTranslationError, match=r"param.*to"):
        parse_param_only_change({"param": "vol_lo", "from": 10})


def test_parse_non_mapping_raises() -> None:
    with pytest.raises(ParamOnlyTranslationError, match="mapping"):
        parse_param_only_change("vol_lo=5")
    with pytest.raises(ParamOnlyTranslationError, match="mapping"):
        parse_param_only_change([{"param": "vol_lo", "to": 5}])


def test_parse_logic_change_keys_routes_caller_elsewhere() -> None:
    with pytest.raises(ParamOnlyTranslationError, match="logic-change"):
        parse_param_only_change({"source": "fn on_bar(...) {}", "diffs": [{"param": "x", "to": 1}]})
    with pytest.raises(ParamOnlyTranslationError, match="logic-change"):
        parse_param_only_change({"rewrite": True})


def test_parse_unrecognized_shape_raises_with_keys() -> None:
    with pytest.raises(ParamOnlyTranslationError, match=r"param.*to.*diffs"):
        parse_param_only_change({"foo": 1, "bar": 2})


def test_apply_param_diffs_preserves_untouched_keys() -> None:
    base = {"vol_lo": 10, "vol_hi": 30, "lookback": 20}
    merged = apply_param_diffs(
        base,
        [
            ParamDiff(param="vol_lo", from_value=10, to_value=5),
            ParamDiff(param="vol_hi", from_value=30, to_value=25),
        ],
    )
    assert merged == {"vol_lo": 5, "vol_hi": 25, "lookback": 20}
    # Returns a copy, not a mutation.
    assert base["vol_lo"] == 10


def test_apply_param_diffs_last_write_wins_for_duplicate_keys() -> None:
    merged = apply_param_diffs(
        {"x": 1},
        [
            ParamDiff(param="x", from_value=1, to_value=2),
            ParamDiff(param="x", from_value=2, to_value=99),
        ],
    )
    assert merged == {"x": 99}


def test_translate_param_only_full_round_trip() -> None:
    cand = _candidate({"param": "vol_lo", "from": 10, "to": 5})
    result = translate_param_only(
        cand,
        strategy_artifact="artifact-abc",
        base_params={"vol_lo": 10, "vol_hi": 30},
    )
    assert result.strategy_artifact == "artifact-abc"
    assert result.params == {"vol_lo": 5, "vol_hi": 30}
    assert result.diffs == [ParamDiff(param="vol_lo", from_value=10, to_value=5)]


def test_translate_param_only_logic_change_raises() -> None:
    cand = _candidate({"source": "fn on_bar(){}", "param": "x", "to": 1})
    with pytest.raises(ParamOnlyTranslationError, match="logic-change"):
        translate_param_only(cand, strategy_artifact="artifact-abc", base_params={})


def test_param_diff_alias_round_trip_via_validate() -> None:
    diff = ParamDiff.model_validate({"param": "x", "from": 1, "to": 2})
    assert diff.from_value == 1
    assert diff.to_value == 2
    # JSON serialization uses field-name (`from_value`) not alias; the
    # ledger stores the parsed shape so this is the audit form.
    payload = diff.model_dump()
    assert payload == {"param": "x", "from_value": 1, "to_value": 2}
