"""Tests for the repair-budget exhaustion control-transfer flow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from strategy_gpt.author import (
    AuthorBudgetExhaustedError,
    AuthorDeps,
    AuthorIntent,
    RepairMenuChoice,
    SmokeRunResult,
    SmokeSpec,
    run_author_session,
)
from strategy_gpt.author_decisions import (
    DecisionRecord,
    RepairBudgetExhausted,
    decision_record_path_for,
)
from strategy_gpt.build_pipeline import (
    BuildErrorKind,
    BuildFailure,
    LintReport,
    StrategyManifest,
)
from strategy_gpt.repair import RepairConfig


class _AlwaysFailingPipeline:
    def lint(self, source: str, manifest: StrategyManifest) -> LintReport:
        del source, manifest
        return LintReport(ok=True, source_violations=[], manifest_violations=[])

    def build(self, source: str, manifest: StrategyManifest) -> Any:
        del source, manifest
        raise BuildFailure(BuildErrorKind.CARGO, "synthetic compile error")


class _RecordingClient:
    """Reasoning client capturing every call; emits a canned emission."""

    def __init__(self, emit_payload: str, dialog_payload: str | None = None) -> None:
        self._emit_payload = emit_payload
        self._dialog_payload = dialog_payload or ""
        self.emit_calls: int = 0
        self.dialog_calls: int = 0

    def dialog_turn(self, *, system: str, transcript: list[dict[str, str]]) -> str:
        del system, transcript
        self.dialog_calls += 1
        return self._dialog_payload

    def emit_files(self, *, system: str, user: str) -> str:
        del system, user
        self.emit_calls += 1
        return self._emit_payload


def _intent(name: str = "spy-strat") -> AuthorIntent:
    return AuthorIntent(
        name=name,
        description="d",
        mechanism_summary="m",
        param_schema_sketch={"params": []},
        smoke_spec=SmokeSpec(
            symbol="SPY",
            resolution="1d",
            start="2023-01-01",
            end="2023-03-01",
            provider="yfinance",
        ),
    )


def _passing_smoke(_: Path, _spec: SmokeSpec) -> SmokeRunResult:
    return SmokeRunResult(ok=True, feedback="trades=1")


def _emission(name: str = "spy-strat") -> str:
    return f"""\
## Cargo.toml
```toml
[package]
name = "{name}-strategy"
version = "0.1.0"

[dependencies]
engine-rt = {{ path = "../engine-rt" }}
```

## src/lib.rs
```rust
// stub
```

## smoke.toml
```toml
symbol = "SPY"
resolution = "1d"
start = "2023-01-01"
end = "2023-03-01"
provider = "yfinance"
```
"""


@pytest.fixture
def crates_dir(tmp_path: Path) -> Path:
    root = tmp_path / "crates"
    root.mkdir()
    (root / "build-pipeline").mkdir()
    (root / "build-pipeline" / "whitelist.toml").write_text("schema_version = 1\n")
    return root


def _deps(crates_dir: Path, client: _RecordingClient, *, k: int = 0) -> AuthorDeps:
    return AuthorDeps(
        reasoning_client=client,
        build_pipeline=_AlwaysFailingPipeline(),
        smoke_runner=_passing_smoke,
        crates_dir=crates_dir,
        repair_config_emit=RepairConfig(k_repair=k),
        decision_record_path=decision_record_path_for(crates_dir / "spy-strat-strategy"),
    )


def test_abort_re_raises_and_records_exhaustion(crates_dir: Path) -> None:
    """Option 4 (abort) re-raises the exhaustion and writes the event."""
    record_path = decision_record_path_for(crates_dir / "spy-strat-strategy")
    record_path.parent.mkdir(parents=True, exist_ok=True)
    client = _RecordingClient(_emission())

    def menu(_exc: AuthorBudgetExhaustedError) -> RepairMenuChoice:
        return RepairMenuChoice(kind="abort", payload={})

    with pytest.raises(AuthorBudgetExhaustedError):
        run_author_session(
            _intent(),
            deps=_deps(crates_dir, client),
            reasoning_client=client,
            repair_menu=menu,
            write_user=lambda _: None,
        )

    events = DecisionRecord.load(record_path)
    assert any(isinstance(e, RepairBudgetExhausted) for e in events)


def test_extend_budget_retries_with_new_k(crates_dir: Path) -> None:
    """Option 2 (extend budget) re-runs with the new budget. Still failing ⇒ second exhaustion."""
    client = _RecordingClient(_emission())
    calls: list[int] = []

    def menu(_exc: AuthorBudgetExhaustedError) -> RepairMenuChoice:
        if not calls:
            calls.append(1)
            return RepairMenuChoice(kind="extend_budget", payload={"k_repair_emit": 1})
        return RepairMenuChoice(kind="abort", payload={})

    with pytest.raises(AuthorBudgetExhaustedError):
        run_author_session(
            _intent(),
            deps=_deps(crates_dir, client, k=0),
            reasoning_client=client,
            repair_menu=menu,
            write_user=lambda _: None,
        )
    # First failure: 1 emit call. Second attempt with k_repair_emit=1 ⇒ 2 more calls. Total 3.
    expected_emit_calls = 3
    assert client.emit_calls == expected_emit_calls


def test_suggest_alternative_amends_intent_and_retries(crates_dir: Path) -> None:
    """Option 1 (suggest alternative) dispatches an LLM amend turn, then retries."""
    dialog_payload = """\
# AuthorIntent
```yaml
name: spy-strat
description: |
  amended
mechanism_summary: |
  Bollinger breakout instead of ATR
param_schema_sketch:
  params: []
smoke_spec:
  symbol: SPY
  resolution: 1d
  start: 2023-01-01
  end: 2023-03-01
  provider: yfinance
```
"""
    client = _RecordingClient(_emission(), dialog_payload=dialog_payload)

    def menu(_exc: AuthorBudgetExhaustedError) -> RepairMenuChoice:
        if client.dialog_calls == 0:
            return RepairMenuChoice(
                kind="suggest_alternative",
                payload={"guidance": "try Bollinger breakout instead"},
            )
        return RepairMenuChoice(kind="abort", payload={})

    with pytest.raises(AuthorBudgetExhaustedError):
        run_author_session(
            _intent(),
            deps=_deps(crates_dir, client),
            reasoning_client=client,
            repair_menu=menu,
            write_user=lambda _: None,
        )

    assert client.dialog_calls == 1


def test_edit_decision_scoped_to_field(crates_dir: Path) -> None:
    """Option 3 (edit a specific decision) calls the amend helper with scope_field set."""
    captured_system_prompts: list[str] = []
    dialog_payload = """\
# AuthorIntent
```yaml
name: spy-strat
description: |
  d
mechanism_summary: |
  m
param_schema_sketch:
  params: [{ name: window, kind: i64, default: 20 }]
smoke_spec:
  symbol: SPY
  resolution: 1d
  start: 2023-01-01
  end: 2023-03-01
  provider: yfinance
```
"""

    class _CapturingClient(_RecordingClient):
        def dialog_turn(self, *, system: str, transcript: list[dict[str, str]]) -> str:
            captured_system_prompts.append(system)
            return super().dialog_turn(system=system, transcript=transcript)

    client = _CapturingClient(_emission(), dialog_payload=dialog_payload)

    def menu(_exc: AuthorBudgetExhaustedError) -> RepairMenuChoice:
        if client.dialog_calls == 0:
            return RepairMenuChoice(
                kind="edit_decision",
                payload={"field": "param_sketch", "guidance": "widen the window default to 50"},
            )
        return RepairMenuChoice(kind="abort", payload={})

    with pytest.raises(AuthorBudgetExhaustedError):
        run_author_session(
            _intent(),
            deps=_deps(crates_dir, client),
            reasoning_client=client,
            repair_menu=menu,
            write_user=lambda _: None,
        )

    assert captured_system_prompts, "amend helper should have dispatched a dialog turn"
    assert "Revise ONLY the `param_sketch` field" in captured_system_prompts[0]
