"""CLI-level tests for the ``author`` command.

Tests inject stub reasoning and smoke runners via the module-level
factory hooks declared in :mod:`strategy_gpt.cli`. The real
:class:`BuildPipeline` is monkeypatched with a stub so the tests do not
require the native module or a real cargo build.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from strategy_gpt import cli
from strategy_gpt.author import SmokeRunResult, SmokeSpec
from strategy_gpt.build_pipeline import (
    BuildArtifact,
    BuildOutcome,
    BuildOutcomeKind,
    LintReport,
    StrategyManifest,
)
from strategy_gpt.types import RunnerVersion

runner = CliRunner()


_AUTHOR_INTENT_YAML = """\
# AuthorIntent
```yaml
name: cli_strat
description: |
  Test strategy authored via CLI.
mechanism_summary: |
  No-op buy and hold.
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


_EMIT_PAYLOAD = """\
## Cargo.toml
```toml
[package]
name = "cli_strat-strategy"
version = "0.1.0"
edition = "2021"

[lib]
crate-type = ["cdylib"]

[dependencies]
engine-rt = { path = "../engine-rt" }
```

## src/lib.rs
```rust
// stub body
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


class _StubAuthorClient:
    def __init__(self) -> None:
        self.dialog_calls = 0
        self.emit_calls = 0

    def dialog_turn(self, *, system: str, transcript: list[dict[str, str]]) -> str:
        del system, transcript
        self.dialog_calls += 1
        return _AUTHOR_INTENT_YAML

    def emit_files(self, *, system: str, user: str) -> str:
        del system, user
        self.emit_calls += 1
        return _EMIT_PAYLOAD


class _StubBuildPipeline:
    """Minimal pipeline stub: lint passes, build returns a synthetic artifact."""

    def __init__(self, *_: object, **__: object) -> None:
        pass

    def lint(self, source: str, manifest: StrategyManifest) -> LintReport:
        del source, manifest
        return LintReport(ok=True, source_violations=[], manifest_violations=[])

    def build(self, source: str, manifest: StrategyManifest) -> BuildOutcome:
        del source, manifest
        return BuildOutcome(
            kind=BuildOutcomeKind.COMPILED,
            artifact=BuildArtifact(
                key="cli-stub-artifact",
                library_path="(cli-stub)/libcli.so",
                runner_version=RunnerVersion(major=0, minor=1, patch=0),
                source_size_bytes=64,
            ),
        )


def _ok_smoke_factory(**_: object) -> Any:
    def _runner(_path: Path, _spec: SmokeSpec) -> SmokeRunResult:
        return SmokeRunResult(ok=True, artifact_hash="cli-stub-artifact")

    return _runner


@pytest.fixture
def author_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, _StubAuthorClient]:
    """Stand up a tmpdir workspace + monkeypatch the factory hooks."""
    crates_dir = tmp_path / "crates"
    crates_dir.mkdir()
    (crates_dir / "build-pipeline").mkdir()
    (crates_dir / "build-pipeline" / "whitelist.toml").write_text("schema_version = 1\n")

    client = _StubAuthorClient()

    monkeypatch.setattr(cli, "_author_reasoning_client_factory", lambda _model: client)
    monkeypatch.setattr(cli, "_author_smoke_runner_factory", _ok_smoke_factory)
    monkeypatch.setattr("strategy_gpt.cli.BuildPipeline", _StubBuildPipeline, raising=False)
    # `BuildPipeline` is imported lazily inside the command via `from
    # .build_pipeline import BuildPipeline`; patching the module attribute
    # is the cleanest hook.
    import strategy_gpt.build_pipeline as bp_mod  # noqa: PLC0415

    monkeypatch.setattr(bp_mod, "BuildPipeline", _StubBuildPipeline)
    return crates_dir, client


def test_author_happy_path_prints_next_steps(
    author_workspace: tuple[Path, _StubAuthorClient],
) -> None:
    crates_dir, client = author_workspace
    result = runner.invoke(
        cli.app,
        [
            "author",
            "buy and hold SPY",
            "--crates-dir",
            str(crates_dir),
            "--cache-root",
            str(crates_dir.parent / "cache" / "builds"),
            "--work-root",
            str(crates_dir.parent / "cache" / "build-work"),
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["name"] == "cli_strat"
    assert payload["crate_path"].endswith("cli_strat-strategy")
    assert any("hypothesize" in hint for hint in payload["next_steps"])
    assert client.dialog_calls == 1
    assert client.emit_calls == 1


def test_author_rejects_unknown_verify_value(
    author_workspace: tuple[Path, _StubAuthorClient],
) -> None:
    crates_dir, _ = author_workspace
    result = runner.invoke(
        cli.app,
        [
            "author",
            "--crates-dir",
            str(crates_dir),
            "--verify",
            "live",
        ],
    )
    assert result.exit_code != 0
    assert "verify" in (result.stdout + result.stderr).lower()


def test_author_help_documents_surface() -> None:
    result = runner.invoke(cli.app, ["author", "--help"])
    assert result.exit_code == 0
    for opt in ("--verify", "--k-repair-emit", "--k-repair-build", "--model", "--crates-dir"):
        assert opt in result.stdout
