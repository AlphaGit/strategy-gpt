"""Tests for stage-1/2/3 validators wiring markdown_io + build_pipeline."""

from __future__ import annotations

from typing import Any

import pytest

from strategy_gpt.build_pipeline import (
    BuildArtifact,
    BuildErrorKind,
    BuildFailure,
    BuildOutcome,
    BuildOutcomeKind,
    LintReport,
    StrategyManifest,
)
from strategy_gpt.markdown_io import Stage1Idea, Stage2Commitments
from strategy_gpt.types import RunnerVersion
from strategy_gpt.validation import (
    validate_stage1,
    validate_stage2,
    validate_stage3,
)

# ---------- Stage 1 / Stage 2 ----------


def test_validate_stage1_success() -> None:
    text = (
        "# Idea\n\n"
        "candidate_name: x\n"
        "rationale: foo\n"
        "expected_lift_confidence: 0.4\n"
        "expected_side_effects: []\n"
    )
    outcome = validate_stage1(text)
    assert outcome.ok
    assert isinstance(outcome.parsed, Stage1Idea)


def test_validate_stage1_format_rejection() -> None:
    outcome = validate_stage1("not markdown at all")
    assert not outcome.ok
    assert outcome.kind == "reject_format"


def test_validate_stage2_with_metric_allowlist() -> None:
    text = (
        "# Falsification\n\n"
        "```yaml\n"
        "primary: { metric: sharpe, direction: gt, delta_vs_baseline: 0.2 }\n"
        "```\n\n"
        "# ParamIntent\n\n"
        "```yaml\n"
        "added: []\nkept: []\nremoved: []\n"
        "```\n"
    )
    outcome = validate_stage2(text, allowed_metrics=frozenset({"sharpe", "max_drawdown"}))
    assert outcome.ok
    assert isinstance(outcome.parsed, Stage2Commitments)


def test_validate_stage2_rejects_unknown_kept_param() -> None:
    """A stage-2 ``kept`` name that does not appear in the baseline
    schema MUST surface as a repairable ``reject_schema`` so the LLM
    fixes it on the next attempt rather than blowing up later in
    mini-optimize.
    """
    text = (
        "# Falsification\n\n"
        "```yaml\n"
        "primary: { metric: sharpe, direction: gt, delta_vs_baseline: 0.1 }\n"
        "```\n\n"
        "# ParamIntent\n\n"
        "```yaml\n"
        "added: []\nkept: [w_rsi, threshold_entry]\nremoved: []\n"
        "```\n"
    )
    outcome = validate_stage2(
        text,
        allowed_metrics=frozenset({"sharpe"}),
        kept_param_names=frozenset({"threshold_entry"}),
    )
    assert not outcome.ok
    assert outcome.kind == "reject_schema"
    assert "w_rsi" in outcome.feedback
    assert "threshold_entry" in outcome.feedback  # in the allowed list


def test_validate_stage2_accepts_kept_subset_of_allowed() -> None:
    text = (
        "# Falsification\n\n"
        "```yaml\n"
        "primary: { metric: sharpe, direction: gt, delta_vs_baseline: 0.1 }\n"
        "```\n\n"
        "# ParamIntent\n\n"
        "```yaml\n"
        "added: []\nkept: [a]\nremoved: []\n"
        "```\n"
    )
    outcome = validate_stage2(
        text,
        allowed_metrics=frozenset({"sharpe"}),
        kept_param_names=frozenset({"a", "b"}),
    )
    assert outcome.ok


def test_validate_stage2_rejects_unknown_metric() -> None:
    text = (
        "# Falsification\n\n"
        "```yaml\n"
        "primary: { metric: rocket_alpha, direction: gt, delta_vs_baseline: 0.2 }\n"
        "```\n\n"
        "# ParamIntent\n\n"
        "```yaml\n"
        "added: []\nkept: []\nremoved: []\n"
        "```\n"
    )
    outcome = validate_stage2(text, allowed_metrics=frozenset({"sharpe"}))
    assert not outcome.ok
    assert outcome.kind == "reject_format"


# ---------- Stage 3 ----------


_OK_CARGO = """
[package]
name = "candidate_x"
version = "0.1.0"

[dependencies]
engine_rt = { path = "../engine-rt" }
""".strip()

_OK_LIB = "// dummy src/lib.rs\n"
_OK_SCHEMA = '{"schema_version": 1, "params": []}'


def _stage3_text(*, lib: str = _OK_LIB, cargo: str = _OK_CARGO, schema: str = _OK_SCHEMA) -> str:
    return (
        f"## Cargo.toml\n```toml\n{cargo}\n```\n\n"
        f"## src/lib.rs\n```rust\n{lib}\n```\n\n"
        f"## params_schema.json\n```json\n{schema}\n```\n"
    )


class _StubPipeline:
    def __init__(
        self,
        *,
        lint_ok: bool = True,
        build_error: BuildErrorKind | None = None,
        build_message: str = "",
    ) -> None:
        self._lint_ok = lint_ok
        self._build_error = build_error
        self._build_message = build_message
        self.lint_calls: list[tuple[str, StrategyManifest]] = []
        self.build_calls: list[tuple[str, StrategyManifest]] = []

    def lint(self, source: str, manifest: StrategyManifest) -> LintReport:
        self.lint_calls.append((source, manifest))
        return LintReport(
            ok=self._lint_ok,
            source_violations=[] if self._lint_ok else ["unsafe block forbidden"],
            manifest_violations=[],
        )

    def build(self, source: str, manifest: StrategyManifest) -> Any:
        self.build_calls.append((source, manifest))
        if self._build_error is not None:
            raise BuildFailure(kind=self._build_error, message=self._build_message)
        return BuildOutcome(
            kind=BuildOutcomeKind.COMPILED,
            artifact=BuildArtifact(
                key="dummy",
                library_path="/var/tmp/libdummy.so",  # noqa: S108 — stub artifact path, not a real fs op
                runner_version=RunnerVersion(major=0, minor=1, patch=0),
                source_size_bytes=len(source),
            ),
        )


def test_stage3_happy_path_invokes_lint_and_build() -> None:
    pipeline = _StubPipeline()
    outcome = validate_stage3(_stage3_text(), pipeline=pipeline)
    assert outcome.ok
    assert len(pipeline.lint_calls) == 1
    assert len(pipeline.build_calls) == 1
    parsed = outcome.parsed
    assert isinstance(parsed, dict)
    assert "files" in parsed
    assert "build_outcome" in parsed


def test_stage3_missing_required_file() -> None:
    text = "## Cargo.toml\n```toml\n[package]\nname='x'\nversion='0.1.0'\n```\n"
    outcome = validate_stage3(text, pipeline=_StubPipeline())
    assert not outcome.ok
    assert outcome.kind == "reject_format"
    assert "src/lib.rs" in outcome.feedback


def test_stage3_invalid_cargo_toml() -> None:
    text = _stage3_text(cargo="not toml at all = ===")
    outcome = validate_stage3(text, pipeline=_StubPipeline())
    assert not outcome.ok
    assert outcome.kind == "reject_format"


def test_stage3_param_intent_added_not_in_schema() -> None:
    text = _stage3_text(schema='{"schema_version": 1, "params": []}')
    outcome = validate_stage3(
        text,
        pipeline=_StubPipeline(),
        stage2_param_intent={"added": [{"name": "vol_threshold"}], "removed": []},
    )
    assert not outcome.ok
    assert outcome.kind == "reject_schema"
    assert "vol_threshold" in outcome.feedback


def test_stage3_param_intent_removed_still_in_schema() -> None:
    text = _stage3_text(
        schema='{"schema_version": 1, "params": [{"name": "vol_lo", "kind": "f64",'
        ' "min": 0.0, "max": 1.0, "default": 0.5}]}'
    )
    outcome = validate_stage3(
        text,
        pipeline=_StubPipeline(),
        stage2_param_intent={"added": [], "removed": ["vol_lo"]},
    )
    assert not outcome.ok
    assert outcome.kind == "reject_schema"


def test_stage3_lint_rejection() -> None:
    pipeline = _StubPipeline(lint_ok=False)
    outcome = validate_stage3(_stage3_text(), pipeline=pipeline)
    assert not outcome.ok
    assert outcome.kind == "reject_lint"
    assert "unsafe block forbidden" in outcome.feedback


def test_stage3_build_failure_maps_to_reject_build() -> None:
    pipeline = _StubPipeline(
        build_error=BuildErrorKind.CARGO,
        build_message="error[E0308]: mismatched types\nfoo\nbar",
    )
    outcome = validate_stage3(_stage3_text(), pipeline=pipeline)
    assert not outcome.ok
    assert outcome.kind == "reject_build"
    assert "mismatched types" in outcome.feedback


def test_stage3_whitelist_error_maps_to_reject_deps() -> None:
    pipeline = _StubPipeline(
        build_error=BuildErrorKind.WHITELIST,
        build_message="crate `tokio` not on the whitelist",
    )
    outcome = validate_stage3(_stage3_text(), pipeline=pipeline)
    assert not outcome.ok
    assert outcome.kind == "reject_deps"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
