"""Event-stream tests for the author emit/build/smoke loop."""

from __future__ import annotations

from pathlib import Path

import pytest

from strategy_gpt.author import (
    AuthorDeps,
    AuthorIntent,
    SmokeRunResult,
    SmokeSpec,
    author_strategy,
)
from strategy_gpt.author_events import (
    AuthorEvent,
    CargoBuildCompleted,
    CargoBuildStarted,
    FileWritten,
    LintCompleted,
    LintStarted,
    RepairAttemptCompleted,
    RepairAttemptStarted,
    SmokeFetchCompleted,
    SmokeFetchStarted,
    SmokeRunCompleted,
    SmokeRunStarted,
    collecting_sink,
)
from strategy_gpt.build_pipeline import (
    BuildArtifact,
    BuildOutcome,
    BuildOutcomeKind,
    LintReport,
    StrategyManifest,
)
from strategy_gpt.repair import RepairConfig
from strategy_gpt.types import RunnerVersion


class _StubBuildPipeline:
    def lint(self, source: str, manifest: StrategyManifest) -> LintReport:
        del source, manifest
        return LintReport(ok=True, source_violations=[], manifest_violations=[])

    def build(self, source: str, manifest: StrategyManifest) -> BuildOutcome:
        del source, manifest
        return BuildOutcome(
            kind=BuildOutcomeKind.COMPILED,
            artifact=BuildArtifact(
                key="stub-key",
                library_path="(stub)/libstrategy.so",
                runner_version=RunnerVersion(major=0, minor=1, patch=0),
                source_size_bytes=64,
            ),
        )


class _OneShotClient:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def dialog_turn(self, *, system: str, transcript: list[dict[str, str]]) -> str:
        del system, transcript
        msg = "dialog_turn unused in event-stream tests"
        raise NotImplementedError(msg)

    def emit_files(self, *, system: str, user: str) -> str:
        del system, user
        return self._payload


def _passing_smoke(_: Path, _spec: SmokeSpec) -> SmokeRunResult:
    return SmokeRunResult(ok=True, feedback="trades=3, sanity_trips=0")


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


def _emission(name: str = "spy-strat") -> str:
    return f"""\
## Cargo.toml
```toml
[package]
name = "{name}-strategy"
version = "0.1.0"
edition = "2021"

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


def test_successful_run_emits_expected_event_sequence(crates_dir: Path) -> None:
    """A passing emit/build/smoke run yields the canonical event order."""
    events, sink = collecting_sink()
    intent = _intent()
    deps = AuthorDeps(
        reasoning_client=_OneShotClient(_emission()),
        build_pipeline=_StubBuildPipeline(),
        smoke_runner=_passing_smoke,
        crates_dir=crates_dir,
        repair_config_emit=RepairConfig(k_repair=0),
        event_sink=sink,
    )

    author_strategy(intent, deps=deps)

    kinds = [type(e) for e in events]
    expected_prefix: list[type[AuthorEvent]] = [
        RepairAttemptStarted,
        FileWritten,
        FileWritten,
        FileWritten,
        LintStarted,
        LintCompleted,
        CargoBuildStarted,
        CargoBuildCompleted,
        SmokeFetchStarted,
        SmokeFetchCompleted,
        SmokeRunStarted,
        SmokeRunCompleted,
        RepairAttemptCompleted,
    ]
    # FileWritten count is exactly 3 (Cargo.toml, src/lib.rs, smoke.toml).
    file_writes = [e for e in events if isinstance(e, FileWritten)]
    expected_file_count = 3
    assert len(file_writes) == expected_file_count
    assert kinds == expected_prefix


def test_smoke_run_completed_carries_trade_count(crates_dir: Path) -> None:
    """``SmokeRunCompleted`` reports trades / sanity_trips from feedback."""
    events, sink = collecting_sink()
    deps = AuthorDeps(
        reasoning_client=_OneShotClient(_emission()),
        build_pipeline=_StubBuildPipeline(),
        smoke_runner=lambda _, _spec: SmokeRunResult(
            ok=True, feedback="trades=7, sanity_trips=2"
        ),
        crates_dir=crates_dir,
        repair_config_emit=RepairConfig(k_repair=0),
        event_sink=sink,
    )

    author_strategy(_intent(), deps=deps)

    completed = next(e for e in events if isinstance(e, SmokeRunCompleted))
    expected_trades = 7
    expected_sanity = 2
    assert completed.trade_count == expected_trades
    assert completed.sanity_trips == expected_sanity


def test_default_sink_writes_nothing_to_stdout(
    crates_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Library callers that do not pass ``event_sink`` see no stdout chatter."""
    deps = AuthorDeps(
        reasoning_client=_OneShotClient(_emission()),
        build_pipeline=_StubBuildPipeline(),
        smoke_runner=_passing_smoke,
        crates_dir=crates_dir,
        repair_config_emit=RepairConfig(k_repair=0),
    )

    author_strategy(_intent(), deps=deps)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
