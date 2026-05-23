"""Tests for the author command and library seam."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from strategy_gpt.author import (
    AuthorBudgetExhaustedError,
    AuthorDeps,
    AuthorIntent,
    DialogError,
    SmokeRunResult,
    SmokeSpec,
    author_strategy,
    crate_dir_for,
    load_intent_toml,
    run_intent_dialog,
)
from strategy_gpt.build_pipeline import (
    BuildArtifact,
    BuildErrorKind,
    BuildFailure,
    BuildOutcome,
    BuildOutcomeKind,
    LintReport,
    StrategyManifest,
)
from strategy_gpt.repair import RepairConfig
from strategy_gpt.types import RunnerVersion

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _ScriptedDialogClient:
    """Returns a canned sequence of dialog responses."""

    def __init__(self, dialog_turns: list[str], emissions: list[str] | None = None) -> None:
        self._dialog = list(dialog_turns)
        self._emit = list(emissions or [])
        self.dialog_calls: list[list[dict[str, str]]] = []
        self.emit_calls: list[tuple[str, str]] = []

    def dialog_turn(self, *, system: str, transcript: list[dict[str, str]]) -> str:
        self.dialog_calls.append([dict(m) for m in transcript])
        del system
        if not self._dialog:
            raise AssertionError("dialog client exhausted")
        return self._dialog.pop(0)

    def emit_files(self, *, system: str, user: str) -> str:
        self.emit_calls.append((system, user))
        if not self._emit:
            raise AssertionError("emit client exhausted")
        return self._emit.pop(0)


class _StubBuildPipeline:
    """Configurable build-pipeline stub.

    ``lint_ok`` toggles the lint verdict; ``build_outcomes`` is a queue
    of build verdicts (``BuildOutcome`` for success, ``BuildFailure``
    for the failure branch).
    """

    def __init__(
        self,
        *,
        build_outcomes: list[BuildOutcome | BuildFailure] | None = None,
        lint_outcomes: list[LintReport] | None = None,
    ) -> None:
        self._builds = list(build_outcomes or [_default_build_outcome()])
        self._lints = list(lint_outcomes or [])
        self.lint_calls = 0
        self.build_calls = 0

    def lint(self, source: str, manifest: StrategyManifest) -> LintReport:
        del source, manifest
        self.lint_calls += 1
        if self._lints:
            return self._lints.pop(0)
        return LintReport(ok=True, source_violations=[], manifest_violations=[])

    def build(self, source: str, manifest: StrategyManifest) -> BuildOutcome:
        del source, manifest
        self.build_calls += 1
        if not self._builds:
            raise AssertionError("build pipeline stub exhausted")
        outcome = self._builds.pop(0)
        if isinstance(outcome, BuildFailure):
            raise outcome
        return outcome


def _default_build_outcome() -> BuildOutcome:
    return BuildOutcome(
        kind=BuildOutcomeKind.COMPILED,
        artifact=BuildArtifact(
            key="stub-artifact-key",
            library_path="(stub)/libstrategy.so",
            runner_version=RunnerVersion(major=0, minor=1, patch=0),
            source_size_bytes=128,
        ),
    )


def _passing_smoke(_: Path, __: SmokeSpec) -> SmokeRunResult:
    return SmokeRunResult(ok=True, artifact_hash="stub-artifact-key")


def _failing_smoke(message: str) -> Callable[[Path, SmokeSpec], SmokeRunResult]:
    def _runner(_: Path, __: SmokeSpec) -> SmokeRunResult:
        return SmokeRunResult(ok=False, feedback=message)

    return _runner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def crates_dir(tmp_path: Path) -> Path:
    root = tmp_path / "crates"
    root.mkdir()
    # Minimal exemplar stubs so prompts_author has something to embed —
    # the prompt builder skips missing files but the smoke run benefits
    # from a realistic shape.
    (root / "build-pipeline").mkdir()
    (root / "build-pipeline" / "whitelist.toml").write_text("schema_version = 1\n")
    return root


def _example_intent(name: str = "test_strat") -> AuthorIntent:
    return AuthorIntent(
        name=name,
        description="Test strategy",
        mechanism_summary="Buy on Mondays, sell on Fridays.",
        param_schema_sketch={"params": []},
        smoke_spec=SmokeSpec(
            symbol="SPY",
            resolution="1d",
            start="2023-01-01",
            end="2023-03-01",
            provider="yfinance",
        ),
    )


def _emit_payload(*, name: str = "test_strat") -> str:
    return f"""\
## Cargo.toml
```toml
[package]
name = "{name}-strategy"
version = "0.1.0"
edition = "2021"

[lib]
crate-type = ["cdylib"]

[dependencies]
engine-rt = {{ path = "../engine-rt" }}
```

## src/lib.rs
```rust
// stub strategy body
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


def _emit_payload_non_whitelisted() -> str:
    return """\
## Cargo.toml
```toml
[package]
name = "test_strat-strategy"
version = "0.1.0"
edition = "2021"

[lib]
crate-type = ["cdylib"]

[dependencies]
engine-rt = { path = "../engine-rt" }
reqwest = "0.11"
```

## src/lib.rs
```rust
// non-whitelisted dep present
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


# ---------------------------------------------------------------------------
# Dialog tests
# ---------------------------------------------------------------------------


_HAPPY_PATH_INTENT = """\
# AuthorIntent
```yaml
name: spy_basic
description: |
  Test SPY long-only strategy.
mechanism_summary: |
  Goes long when 20-day MA crosses above 50-day MA.
param_schema_sketch:
  params:
    - { name: fast, kind: i64, min: 5, max: 30, default: 20 }
    - { name: slow, kind: i64, min: 20, max: 100, default: 50 }
smoke_spec:
  symbol: SPY
  resolution: 1d
  start: 2023-01-01
  end: 2023-06-01
  provider: yfinance
```
"""


def test_dialog_happy_path_no_seed(crates_dir: Path) -> None:
    client = _ScriptedDialogClient(dialog_turns=[_HAPPY_PATH_INTENT])
    intent = run_intent_dialog(
        seed=None,
        reasoning_client=client,
        crates_dir=crates_dir,
        ask_user=lambda _: "",
        write_user=lambda _: None,
    )
    assert intent.name == "spy_basic"
    assert intent.smoke_spec.symbol == "SPY"
    assert intent.baseline_crate is None
    # The very first dialog turn carried the placeholder for the missing seed.
    assert "no seed supplied" in client.dialog_calls[0][0]["content"]


def test_dialog_with_seed_threaded_into_first_turn(crates_dir: Path) -> None:
    client = _ScriptedDialogClient(dialog_turns=[_HAPPY_PATH_INTENT])
    run_intent_dialog(
        seed="trend-follow SPY",
        reasoning_client=client,
        crates_dir=crates_dir,
        ask_user=lambda _: "",
        write_user=lambda _: None,
    )
    assert client.dialog_calls[0][0]["content"] == "trend-follow SPY"


def test_dialog_clarifying_question_then_intent(crates_dir: Path) -> None:
    replies = iter(["use SPY"])
    client = _ScriptedDialogClient(
        dialog_turns=["What instrument should I target?", _HAPPY_PATH_INTENT]
    )
    captured: list[str] = []
    intent = run_intent_dialog(
        seed=None,
        reasoning_client=client,
        crates_dir=crates_dir,
        ask_user=lambda _: next(replies),
        write_user=captured.append,
    )
    assert intent.name == "spy_basic"
    assert captured == ["What instrument should I target?"]
    assert len(client.dialog_calls) == 2


def test_dialog_edit_mode_auto_detected(crates_dir: Path) -> None:
    # Pre-create the colliding crate so the dialog detects edit-mode.
    existing = crate_dir_for(crates_dir, "spy_basic")
    existing.mkdir()
    (existing / "Cargo.toml").write_text("[package]\nname = 'spy_basic-strategy'\n")

    client = _ScriptedDialogClient(dialog_turns=[_HAPPY_PATH_INTENT])
    intent = run_intent_dialog(
        seed=None,
        reasoning_client=client,
        crates_dir=crates_dir,
        ask_user=lambda _: "edit",
        write_user=lambda _: None,
    )
    assert intent.baseline_crate == existing


_VERIFY_BATCH_INTENT = """\
# AuthorIntent
```yaml
name: spy_basic
description: Strategy under --verify=batch.
mechanism_summary: |
  Long-only daily strat with MA crossover.
param_schema_sketch: {}
smoke_spec:
  symbol: SPY
  resolution: 1d
  start: 2023-01-01
  end: 2023-06-01
  provider: yfinance
experiment_spec:
  strategy_artifact: blake3:placeholder
  runs:
    - { params: {}, modes: [{ kind: plain }], seed: 0 }
```
"""


def test_dialog_verify_batch_populates_experiment_spec(crates_dir: Path) -> None:
    client = _ScriptedDialogClient(dialog_turns=[_VERIFY_BATCH_INTENT])
    intent = run_intent_dialog(
        seed=None,
        reasoning_client=client,
        crates_dir=crates_dir,
        ask_user=lambda _: "",
        write_user=lambda _: None,
    )
    assert intent.experiment_spec is not None
    assert "runs" in intent.experiment_spec


def test_dialog_rejects_invalid_intent_yaml(crates_dir: Path) -> None:
    bad = """\
# AuthorIntent
```yaml
name: 99-bad-start
description: x
mechanism_summary: y
smoke_spec: { symbol: SPY }
```
"""
    client = _ScriptedDialogClient(dialog_turns=[bad])
    with pytest.raises(DialogError):
        run_intent_dialog(
            seed=None,
            reasoning_client=client,
            crates_dir=crates_dir,
            ask_user=lambda _: "",
            write_user=lambda _: None,
        )


# ---------------------------------------------------------------------------
# author_strategy tests
# ---------------------------------------------------------------------------


def _deps(
    client: _ScriptedDialogClient,
    crates_dir: Path,
    *,
    build_pipeline: _StubBuildPipeline | None = None,
    smoke: Callable[[Path, SmokeSpec], SmokeRunResult] = _passing_smoke,
    k_repair: int = 2,
) -> AuthorDeps:
    return AuthorDeps(
        reasoning_client=client,
        build_pipeline=build_pipeline or _StubBuildPipeline(),
        smoke_runner=smoke,
        crates_dir=crates_dir,
        repair_config_emit=RepairConfig(k_repair=k_repair),
        repair_config_build=RepairConfig(k_repair=k_repair),
    )


def test_author_strategy_happy_path(crates_dir: Path) -> None:
    client = _ScriptedDialogClient(dialog_turns=[], emissions=[_emit_payload()])
    result = author_strategy(_example_intent(), deps=_deps(client, crates_dir))
    assert result.name == "test_strat"
    assert (result.crate_path / "src" / "lib.rs").exists()
    assert (result.crate_path / "Cargo.toml").exists()
    assert (result.crate_path / "smoke.toml").exists()
    intent_path = result.crate_path / "intent.toml"
    assert intent_path.exists()
    # Intent round-trips back to the same data.
    reloaded = load_intent_toml(result.crate_path)
    assert reloaded.name == "test_strat"
    assert reloaded.smoke_spec.symbol == "SPY"


def test_author_strategy_non_whitelisted_recovers(crates_dir: Path) -> None:
    """First emission declares reqwest → build rejects → repair fixes it.

    Also asserts the repair-attempt user prompt carries (a) the rustc/whitelist
    feedback and (b) the LLM's previous emission so it can revise instead of
    re-deriving from scratch.
    """
    client = _ScriptedDialogClient(
        dialog_turns=[],
        emissions=[_emit_payload_non_whitelisted(), _emit_payload()],
    )
    bp = _StubBuildPipeline(
        build_outcomes=[
            BuildFailure(BuildErrorKind.WHITELIST, "crate `reqwest` is not in the whitelist"),
            _default_build_outcome(),
        ]
    )
    result = author_strategy(_example_intent(), deps=_deps(client, crates_dir, build_pipeline=bp))
    assert result.name == "test_strat"
    # Two emit attempts were issued; the second carries feedback + previous emission.
    expected_attempts = 2
    assert len(client.emit_calls) == expected_attempts
    second_user_prompt = client.emit_calls[1][1]
    assert "reqwest" in second_user_prompt
    assert "Your previous attempt" in second_user_prompt
    # The first emission body shows up verbatim in the second prompt's previous-attempt section.
    assert _emit_payload_non_whitelisted().strip() in second_user_prompt


def test_author_strategy_budget_exhausted_raises(crates_dir: Path) -> None:
    """Persistent build failures consume the budget; control returns to dialog."""
    payloads = [_emit_payload_non_whitelisted()] * 3
    client = _ScriptedDialogClient(dialog_turns=[], emissions=payloads)
    bp = _StubBuildPipeline(
        build_outcomes=[
            BuildFailure(BuildErrorKind.WHITELIST, "non-whitelisted dep") for _ in range(3)
        ]
    )
    with pytest.raises(AuthorBudgetExhaustedError):
        author_strategy(
            _example_intent(),
            deps=_deps(client, crates_dir, build_pipeline=bp, k_repair=2),
        )


def test_author_strategy_smoke_failure_feeds_back(crates_dir: Path) -> None:
    """A smoke failure surfaces into the next emit attempt's feedback."""
    client = _ScriptedDialogClient(
        dialog_turns=[],
        emissions=[_emit_payload(), _emit_payload()],
    )
    smoke_calls = [0]

    def smoke(_: Path, __: SmokeSpec) -> SmokeRunResult:
        smoke_calls[0] += 1
        if smoke_calls[0] == 1:
            return SmokeRunResult(ok=False, feedback="smoke_failed: no_trades")
        return SmokeRunResult(ok=True, artifact_hash="stub-artifact-key")

    bp = _StubBuildPipeline(build_outcomes=[_default_build_outcome(), _default_build_outcome()])
    result = author_strategy(
        _example_intent(), deps=_deps(client, crates_dir, build_pipeline=bp, smoke=smoke)
    )
    assert result.name == "test_strat"
    assert "no_trades" in client.emit_calls[1][1]


def test_author_strategy_persists_experiment_yaml(crates_dir: Path) -> None:
    intent = AuthorIntent(
        name="batched",
        description="batched strategy",
        mechanism_summary="x",
        param_schema_sketch={},
        smoke_spec=SmokeSpec(
            symbol="SPY",
            resolution="1d",
            start="2023-01-01",
            end="2023-03-01",
        ),
        experiment_spec={"runs": [{"params": {}, "modes": [{"kind": "plain"}], "seed": 0}]},
    )
    client = _ScriptedDialogClient(dialog_turns=[], emissions=[_emit_payload(name="batched")])
    result = author_strategy(intent, deps=_deps(client, crates_dir))
    assert (result.crate_path / "experiment.yaml").exists()
    reloaded = load_intent_toml(result.crate_path)
    assert reloaded.experiment_spec is not None
    assert "runs" in reloaded.experiment_spec


def test_author_strategy_rejects_missing_file(crates_dir: Path) -> None:
    """A payload missing one of the required files is reject_format."""
    payload_missing_smoke = """\
## Cargo.toml
```toml
[package]
name = "x-strategy"
version = "0.1.0"

[dependencies]
engine-rt = { path = "../engine-rt" }
```

## src/lib.rs
```rust
// no smoke.toml
```
"""
    client = _ScriptedDialogClient(
        dialog_turns=[],
        emissions=[payload_missing_smoke, _emit_payload()],
    )
    result = author_strategy(_example_intent(), deps=_deps(client, crates_dir))
    assert result.name == "test_strat"
    assert "smoke.toml" in client.emit_calls[1][1]


# ---------------------------------------------------------------------------
# Schema round-trip
# ---------------------------------------------------------------------------


def test_normalize_cargo_rewrites_registry_dep() -> None:
    """An LLM-emitted ``engine-rt = "*"`` is rewritten to a path dep on disk."""
    from strategy_gpt.author import _normalize_cargo_toml  # noqa: PLC0415

    raw = '[package]\nname = "x"\n\n[dependencies]\nengine-rt = "*"\nserde = "1"\n'
    normalized = _normalize_cargo_toml(raw)
    assert 'engine-rt = { path = "../engine-rt" }' in normalized
    assert 'engine-rt = "*"' not in normalized
    # Unrelated deps are preserved.
    assert 'serde = "1"' in normalized


def test_normalize_cargo_preserves_existing_path_dep() -> None:
    """A correct path dep is left alone (idempotent normalization)."""
    from strategy_gpt.author import _normalize_cargo_toml  # noqa: PLC0415

    raw = '[package]\nname = "x"\n\n[dependencies]\nengine-rt = { path = "../engine-rt" }\n'
    assert _normalize_cargo_toml(raw) == raw


def test_normalize_cargo_inserts_missing_engine_rt() -> None:
    """If the LLM forgot the engine-rt dep, we inject it under [dependencies]."""
    from strategy_gpt.author import _normalize_cargo_toml  # noqa: PLC0415

    raw = '[package]\nname = "x"\n\n[dependencies]\nserde = "1"\n'
    normalized = _normalize_cargo_toml(raw)
    assert 'engine-rt = { path = "../engine-rt" }' in normalized


def test_intent_round_trip_through_disk(tmp_path: Path) -> None:
    crate_path = tmp_path / "rt-strategy"
    crate_path.mkdir()
    intent = AuthorIntent(
        name="rt",
        description="round-trip\nwith multiple\nlines",
        mechanism_summary="single line",
        param_schema_sketch={"params": [{"name": "x", "kind": "f64", "min": 0.0, "max": 1.0}]},
        smoke_spec=SmokeSpec(
            symbol="SPY",
            resolution="1d",
            start="2023-01-01",
            end="2023-03-01",
        ),
        baseline_crate=Path("/some/baseline"),
    )
    # Use the private helper to write — public surface is the persistence
    # done inside author_strategy; the round-trip semantic still belongs
    # in the load_intent_toml contract.
    from strategy_gpt.author import _intent_to_toml  # noqa: PLC0415 — testing internal writer

    (crate_path / "intent.toml").write_text(_intent_to_toml(intent))
    reloaded = load_intent_toml(crate_path)
    assert reloaded.name == "rt"
    assert reloaded.description == "round-trip\nwith multiple\nlines"
    assert reloaded.param_schema_sketch == intent.param_schema_sketch
    assert reloaded.smoke_spec.symbol == "SPY"
    assert reloaded.baseline_crate == Path("/some/baseline")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
