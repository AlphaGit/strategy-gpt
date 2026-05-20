"""Unit tests for the strict markdown emit + parse contract."""

from __future__ import annotations

import pytest

from strategy_gpt.markdown_io import (
    ParseError,
    Stage1Idea,
    Stage3Files,
    parse_stage1,
    parse_stage2,
    parse_stage3,
    serialize_stage1,
    serialize_stage2,
    serialize_stage3,
)

# ---------------- Stage 1 ----------------


def test_stage1_round_trip() -> None:
    idea = Stage1Idea(
        candidate_name="add_drawdown_guard",
        rationale="Cut tail risk during vol blowouts by pausing entries when drawdown > 20%.",
        expected_lift_confidence=0.55,
        expected_side_effects=["~30% trade count decrease"],
    )
    text = serialize_stage1(idea)
    back = parse_stage1(text)
    assert back == idea


def test_stage1_truncates_long_rationale() -> None:
    long = "x" * 700
    text = serialize_stage1(
        Stage1Idea(
            candidate_name="x",
            rationale=long,
            expected_lift_confidence=0.1,
            expected_side_effects=[],
        )
    )
    parsed = parse_stage1(text)
    assert len(parsed.rationale) == 500


def test_stage1_rejects_missing_section() -> None:
    with pytest.raises(ParseError) as exc:
        parse_stage1("Just some prose, no header.\n")
    assert "missing required H1 section" in str(exc.value)


def test_stage1_rejects_missing_key() -> None:
    text = "# Idea\n\ncandidate_name: x\nrationale: y\n"
    with pytest.raises(ParseError) as exc:
        parse_stage1(text)
    assert "expected_lift_confidence" in str(exc.value)


def test_stage1_rejects_confidence_out_of_range() -> None:
    text = (
        "# Idea\n\n"
        "candidate_name: x\nrationale: y\n"
        "expected_lift_confidence: 1.7\n"
        "expected_side_effects: []\n"
    )
    with pytest.raises(ParseError) as exc:
        parse_stage1(text)
    assert "0.0, 1.0" in str(exc.value)


def test_stage1_rejects_extra_h1_section() -> None:
    text = (
        "# Idea\n\n"
        "candidate_name: x\nrationale: y\n"
        "expected_lift_confidence: 0.1\nexpected_side_effects: []\n"
        "\n# UnexpectedExtra\n\nfoo: bar\n"
    )
    with pytest.raises(ParseError) as exc:
        parse_stage1(text)
    assert "UnexpectedExtra" in str(exc.value)


# ---------------- Stage 2 ----------------


def _stage2_text() -> str:
    return (
        "# Falsification\n\n```yaml\n"
        "primary:\n"
        "  metric: sharpe\n"
        "  direction: gt\n"
        "  delta_vs_baseline: 0.20\n"
        "  scope: { kind: aggregate }\n"
        "guard_constraints:\n"
        "  - { metric: max_drawdown, direction: lte, delta_vs_baseline: 0.05 }\n"
        "  - { metric: trade_count, direction: gte, factor: 0.5 }\n"
        "```\n\n"
        "# ParamIntent\n\n```yaml\n"
        "added:\n"
        "  - { name: dd_cap, kind: f64, min: 0.05, max: 0.25, default: 0.15 }\n"
        "kept: [vol_lo, vol_hi]\n"
        "removed: []\n"
        "```\n"
    )


def test_stage2_round_trip() -> None:
    parsed = parse_stage2(_stage2_text())
    text = serialize_stage2(parsed)
    again = parse_stage2(text)
    assert again == parsed


def test_stage2_rejects_unknown_metric() -> None:
    text = _stage2_text().replace("sharpe", "nonsense_metric")
    with pytest.raises(ParseError) as exc:
        parse_stage2(text, allowed_metrics=frozenset({"sharpe", "max_drawdown", "trade_count"}))
    assert "nonsense_metric" in str(exc.value)


def test_stage2_rejects_invalid_direction() -> None:
    text = _stage2_text().replace("direction: gt", "direction: zz")
    with pytest.raises(ParseError) as exc:
        parse_stage2(text)
    assert "direction" in str(exc.value)


def test_stage2_rejects_invalid_scope_kind() -> None:
    text = _stage2_text().replace("kind: aggregate", "kind: galactic")
    with pytest.raises(ParseError) as exc:
        parse_stage2(text)
    assert "galactic" in str(exc.value)


def test_stage2_rejects_added_without_bounds() -> None:
    bad = (
        "# Falsification\n\n```yaml\n"
        "primary:\n  metric: sharpe\n  direction: gt\n  delta_vs_baseline: 0.1\n```\n\n"
        "# ParamIntent\n\n```yaml\n"
        "added: [{ name: x, kind: f64, default: 0.5 }]\n"
        "kept: []\nremoved: []\n```\n"
    )
    with pytest.raises(ParseError) as exc:
        parse_stage2(bad)
    assert "min" in str(exc.value) or "max" in str(exc.value)


def test_stage2_rejects_missing_section() -> None:
    body = _stage2_text().split("\n# ParamIntent")[0] + "\n"
    with pytest.raises(ParseError) as exc:
        parse_stage2(body)
    assert "ParamIntent" in str(exc.value)


def test_stage2_rejects_duplicate_added_names() -> None:
    bad = (
        "# Falsification\n\n```yaml\n"
        "primary:\n  metric: sharpe\n  direction: gt\n  delta_vs_baseline: 0.1\n```\n\n"
        "# ParamIntent\n\n```yaml\n"
        "added:\n"
        "  - { name: x, kind: f64, min: 0.0, max: 1.0, default: 0.5 }\n"
        "  - { name: x, kind: f64, min: 0.0, max: 2.0, default: 0.5 }\n"
        "kept: []\nremoved: []\n```\n"
    )
    with pytest.raises(ParseError) as exc:
        parse_stage2(bad)
    assert "duplicates" in str(exc.value)


# ---------------- Stage 3 ----------------


def _stage3_text() -> str:
    return (
        "## src/lib.rs\n"
        "```rust\n"
        "use engine_rt::Strategy;\n"
        "// content\n"
        "```\n\n"
        "## Cargo.toml\n"
        "```toml\n"
        '[package]\nname = "x"\n'
        "```\n\n"
        "## DELETE: src/old_module.rs\n"
    )


def test_stage3_round_trip() -> None:
    parsed = parse_stage3(_stage3_text())
    assert "src/lib.rs" in parsed.files
    assert "Cargo.toml" in parsed.files
    assert parsed.deleted == ["src/old_module.rs"]
    text = serialize_stage3(parsed)
    again = parse_stage3(text)
    assert again.files == parsed.files
    assert again.deleted == parsed.deleted


def test_stage3_extracts_file_blocks() -> None:
    parsed = parse_stage3(_stage3_text())
    assert "use engine_rt::Strategy;" in parsed.files["src/lib.rs"]
    assert parsed.files["src/lib.rs"].endswith("\n")


def test_stage3_rejects_h2_without_code_block() -> None:
    bad = "## src/lib.rs\n\nNo code block here.\n"
    with pytest.raises(ParseError) as exc:
        parse_stage3(bad)
    assert "missing fenced code block" in str(exc.value)
    assert "src/lib.rs" in str(exc.value)


def test_stage3_rejects_duplicate_path() -> None:
    bad = "## src/lib.rs\n```rust\nfn a() {}\n```\n\n## src/lib.rs\n```rust\nfn b() {}\n```\n"
    with pytest.raises(ParseError) as exc:
        parse_stage3(bad)
    assert "duplicate" in str(exc.value).lower()


def test_stage3_rejects_invalid_path() -> None:
    bad = "## bad path!\n```\nx\n```\n"
    with pytest.raises(ParseError) as exc:
        parse_stage3(bad)
    assert "outside [A-Za-z0-9_./-]" in str(exc.value)


def test_stage3_rejects_unterminated_fence() -> None:
    bad = "## src/lib.rs\n```rust\nfn x()\n"
    with pytest.raises(ParseError) as exc:
        parse_stage3(bad)
    assert "unterminated" in str(exc.value)


def test_stage3_handles_delete_only_emission() -> None:
    text = "## DELETE: src/old_one.rs\n## DELETE: src/old_two.rs\n"
    parsed = parse_stage3(text)
    assert parsed.files == {}
    assert parsed.deleted == ["src/old_one.rs", "src/old_two.rs"]


def test_stage3_rejects_empty_emission() -> None:
    with pytest.raises(ParseError) as exc:
        parse_stage3("# nothing here\n")
    assert "no `## <path>` file sections" in str(exc.value)


def test_stage3_rejects_duplicate_delete() -> None:
    bad = "## DELETE: x.rs\n## DELETE: x.rs\n"
    with pytest.raises(ParseError) as exc:
        parse_stage3(bad)
    assert "duplicate DELETE" in str(exc.value)


def test_stage3_serializer_sorts_paths_for_determinism() -> None:
    s3 = Stage3Files(
        files={"src/z.rs": "z\n", "src/a.rs": "a\n", "Cargo.toml": "c\n"},
        deleted=[],
    )
    text = serialize_stage3(s3)
    a = text.find("## src/a.rs")
    z = text.find("## src/z.rs")
    c = text.find("## Cargo.toml")
    assert c < a < z
