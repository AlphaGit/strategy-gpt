"""Author command: interactive LLM-driven creation of strategy crates.

The author flow is the root primitive for the research loop. Given a
human's natural-language seed, the dialog stage produces a structured
:class:`AuthorIntent`; the emit stage hands that intent to an LLM which
emits ``src/lib.rs`` + ``Cargo.toml`` + ``smoke.toml`` (and optionally
``experiment.yaml``) into ``crates/<name>-strategy/``. The crate is
built package-scoped and smoke-tested; failures feed back through
:func:`repair.run_stage_with_repair`. On success an ``intent.toml``
record is persisted alongside the source.

Author has no falsification, no ledger row, no verdict. Success means
the crate compiles and smoke passes. The crate directory IS the artifact.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from .author_decisions import (
    DecisionAmended,
    DecisionField,
    DecisionLocked,
    DecisionRecord,
    DialogStarted,
    IntentFinalized,
    RepairBudgetExhausted,
    decision_record_path_for,
)
from .author_events import (
    AuthorEventSink,
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
    noop_sink,
)
from .build_pipeline import BuildFailure, ManifestDep, StrategyManifest, _BuildPipelineLike
from .markdown_io import ParseError, parse_stage3
from .repair import RepairConfig, ValidationOutcome, run_stage_with_repair

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")


class SmokeSpec(BaseModel):
    """Fixture data spec for the smoke backtest.

    Identifies the bars the smoke run feeds into the freshly-built
    strategy. ``provider`` defaults to ``yfinance`` since that is the
    one provider wired into the gateway out of the box.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    resolution: str = "1d"
    start: str
    end: str
    provider: str = "yfinance"

    @field_validator("start", "end", mode="before")
    @classmethod
    def _coerce_date(cls, value: object) -> object:
        # YAML safe_load auto-parses ISO dates into `datetime.date`. Accept
        # both shapes so the dialog YAML doesn't have to quote dates.
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return value


@dataclass(frozen=True)
class AuthorIntent:
    """Frozen, normalized intent produced by the dialog stage.

    Fully describes the strategy the LLM is about to emit. The library
    seam (:func:`author_strategy`) accepts a fully-formed intent so the
    hypothesis loop or other programmatic callers can bypass the
    interactive dialog.
    """

    name: str
    description: str
    mechanism_summary: str
    param_schema_sketch: dict[str, Any]
    smoke_spec: SmokeSpec
    experiment_spec: dict[str, Any] | None = None
    baseline_crate: Path | None = None


@dataclass(frozen=True)
class AuthoredStrategy:
    """Successful author-run result."""

    name: str
    crate_path: Path
    artifact_hash: str
    intent: AuthorIntent


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


class AuthorReasoningClient(Protocol):
    """Author-stage reasoning client.

    Two surfaces: ``dialog_turn`` returns either a clarifying question
    or a final ``# AuthorIntent`` YAML payload; ``emit_files`` returns a
    stage-3-shaped markdown payload describing the files to write.
    """

    def dialog_turn(self, *, system: str, transcript: list[dict[str, str]]) -> str: ...

    def emit_files(self, *, system: str, user: str) -> str: ...


@dataclass(frozen=True)
class SmokeRunResult:
    """Outcome of a smoke backtest.

    ``ok`` flips false on panic, sanity-trip, or zero-trade output;
    ``feedback`` is the LLM-facing diagnostic.
    """

    ok: bool
    feedback: str = ""
    artifact_hash: str = ""


SmokeRunner = Callable[[Path, SmokeSpec], SmokeRunResult]
"""Smoke runner. Takes the build artifact's ``library_path`` and the
smoke spec; returns a :class:`SmokeRunResult`. Wired to the real engine
in the CLI; stubbed in tests."""


@dataclass(frozen=True)
class AuthorDeps:
    """Collaborator handles consumed by :func:`author_strategy`."""

    reasoning_client: AuthorReasoningClient
    build_pipeline: _BuildPipelineLike
    smoke_runner: SmokeRunner
    crates_dir: Path
    repair_config_emit: RepairConfig = field(default_factory=lambda: RepairConfig(k_repair=2))
    repair_config_build: RepairConfig = field(default_factory=lambda: RepairConfig(k_repair=2))
    decision_record_path: Path | None = None
    """Path to a :class:`DecisionRecord` for the run, when one is open.

    Set by the CLI after :func:`run_intent_dialog` opens the record so
    ``author_strategy`` can append events of its own (e.g.,
    ``repair_budget_exhausted``). Library callers that bypass the dialog
    can leave this unset; ``author_strategy`` will then not record
    anything beyond what it already does today.
    """
    event_sink: AuthorEventSink = noop_sink
    """Callable that receives :class:`AuthorEvent` instances during the loop.

    Defaults to a no-op so programmatic callers do not need to know
    about the event stream. The CLI installs a sink that renders events
    as human-readable progress lines.
    """


# ---------------------------------------------------------------------------
# Dialog driver
# ---------------------------------------------------------------------------


_INTENT_HEADER = "# AuthorIntent"


class DialogError(RuntimeError):
    """Dialog terminated without a usable intent (operator aborted, etc.)."""


def run_intent_dialog(  # noqa: PLR0913 — dialog driver naturally takes its full collaborator surface
    seed: str | None,
    *,
    reasoning_client: AuthorReasoningClient,
    crates_dir: Path,
    model_name: str = "unknown",
    on_record_ready: Callable[[DecisionRecord], None] | None = None,
    ask_user: Callable[[str], str] = input,
    write_user: Callable[[str], None] = print,
    quiet: bool = False,
    max_turns: int = 12,
) -> AuthorIntent:
    """Drive the clarifying-question dialog until the LLM emits an intent.

    The first turn carries the optional NL seed. Every LLM turn MUST
    include a ``# DecisionsSoFar`` block whose YAML body lists the
    currently-locked decisions; the driver diffs that block against the
    persisted :class:`DecisionRecord` and appends typed events for any
    new locks or amendments. When the LLM finally emits an ``# AuthorIntent``
    block, an :class:`IntentFinalized` event is appended.

    The DecisionRecord cannot be opened until the operator and the LLM
    agree on a crate name (the file lives under that crate's directory),
    so any decisions surfaced before the first ``crate_name`` lands are
    buffered in memory and flushed once the path is known. ``on_record_ready``
    fires the moment the record is opened so the CLI can pass it to
    ``author_strategy`` via :class:`AuthorDeps`.
    """
    from .author_ui import render_decisions_panel  # noqa: PLC0415 — avoid import cycle
    from .prompts_author import (  # noqa: PLC0415 — avoid import cycle
        build_dialog_system_prompt,
        format_decisions_for_prompt,
    )

    system = build_dialog_system_prompt(crates_dir=crates_dir)
    transcript: list[dict[str, str]] = []
    seed_text = seed.strip() if seed else ""
    if seed_text:
        transcript.append({"role": "user", "content": seed_text})
    else:
        transcript.append(
            {"role": "user", "content": "(no seed supplied; ask what I want to author)"}
        )

    record: DecisionRecord | None = None
    pending: dict[str, Any] = {}  # decisions surfaced before crate_name landed

    for _ in range(max_turns):
        response = reasoning_client.dialog_turn(system=system, transcript=transcript)
        transcript.append({"role": "assistant", "content": response})

        prior_projection = record.project() if record is not None else dict(pending)
        record, pending = _ingest_decisions(
            response=response,
            record=record,
            pending=pending,
            crates_dir=crates_dir,
            seed=seed_text or None,
            model_name=model_name,
            on_record_ready=on_record_ready,
        )

        intent_block = _extract_intent_block(response)
        if intent_block is not None:
            intent = _parse_intent_block(intent_block)
            intent = _maybe_attach_baseline(intent, crates_dir, transcript, ask_user, write_user)
            if record is not None:
                record.append(
                    IntentFinalized(timestamp=_now_iso(), intent=_intent_to_dict(intent))
                )
            return intent

        # Plain conversational turn: surface the assistant text, render
        # the locked-in panel if any decision moved, then read the
        # operator's reply. Inject the current decisions projection so a
        # compacted chat history does not lose the locked-in state.
        write_user(response)
        projection = record.project() if record is not None else dict(pending)
        if not quiet and projection and projection != prior_projection:
            panel = render_decisions_panel(projection)
            if panel:
                write_user(panel)
        reply = ask_user("> ")
        decisions_section = format_decisions_for_prompt(projection)
        next_content = f"{reply}\n\n{decisions_section}" if decisions_section else reply
        transcript.append({"role": "user", "content": next_content})

    msg = f"dialog exceeded {max_turns} turns without producing an AuthorIntent"
    raise DialogError(msg)


def _now_iso() -> str:
    """Return an ISO-8601 UTC timestamp suitable for event records."""
    return datetime.now(UTC).isoformat()


_DECISIONS_HEADER = "# DecisionsSoFar"
_DECISION_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "crate_name",
        "universe",
        "mechanism_summary",
        "param_sketch",
        "smoke_spec",
        "experiment_spec",
        "edit_mode_target",
    }
)


def _extract_decisions_block(text: str) -> dict[str, Any] | None:
    """Parse the YAML body under ``# DecisionsSoFar`` from an LLM response."""
    if _DECISIONS_HEADER not in text:
        return None
    idx = text.index(_DECISIONS_HEADER) + len(_DECISIONS_HEADER)
    tail = text[idx:]
    match = _INTENT_FENCE_RE.search(tail)
    body = match.group(1).strip() if match is not None else tail.strip()
    try:
        data = yaml.safe_load(body)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    return {k: v for k, v in data.items() if k in _DECISION_FIELD_NAMES}


def _ingest_decisions(  # noqa: PLR0913 — driver helper takes the driver's surface
    *,
    response: str,
    record: DecisionRecord | None,
    pending: dict[str, Any],
    crates_dir: Path,
    seed: str | None,
    model_name: str,
    on_record_ready: Callable[[DecisionRecord], None] | None,
) -> tuple[DecisionRecord | None, dict[str, Any]]:
    """Parse ``# DecisionsSoFar`` and append events to the record.

    Returns the (possibly-newly-opened) record and the updated pending
    dict. Pending decisions are flushed to the record once the first
    ``crate_name`` lands (the file lives under that crate's directory).
    """
    block = _extract_decisions_block(response)
    if block is None:
        return record, pending

    if record is None:
        new_pending = {**pending, **block}
        if "crate_name" in new_pending:
            crate_name = new_pending["crate_name"]
            rec_path = decision_record_path_for(crate_dir_for(crates_dir, crate_name))
            record = DecisionRecord.open(rec_path)
            record.append(
                DialogStarted(timestamp=_now_iso(), seed=seed, model=model_name)
            )
            for field_name in _ORDERED_FIELDS:
                if field_name in new_pending:
                    record.append(
                        DecisionLocked(
                            timestamp=_now_iso(),
                            field=field_name,
                            value=new_pending[field_name],
                        )
                    )
            if on_record_ready is not None:
                on_record_ready(record)
            return record, {}
        return record, new_pending

    current = record.project()
    for raw_field, value in block.items():
        diff_field = cast(DecisionField, raw_field)  # validated by _extract_decisions_block
        if diff_field not in current:
            record.append(
                DecisionLocked(timestamp=_now_iso(), field=diff_field, value=value)
            )
        elif current[diff_field] != value:
            record.append(
                DecisionAmended(
                    timestamp=_now_iso(),
                    field=diff_field,
                    old_value=current[diff_field],
                    new_value=value,
                )
            )
    return record, pending


_ORDERED_FIELDS: tuple[DecisionField, ...] = (
    "crate_name",
    "edit_mode_target",
    "universe",
    "mechanism_summary",
    "param_sketch",
    "smoke_spec",
    "experiment_spec",
)


def _intent_to_dict(intent: AuthorIntent) -> dict[str, Any]:
    """Render an :class:`AuthorIntent` as a JSON-friendly dict."""
    data = asdict(intent)
    smoke = intent.smoke_spec
    data["smoke_spec"] = {
        "symbol": smoke.symbol,
        "resolution": smoke.resolution,
        "start": smoke.start,
        "end": smoke.end,
        "provider": smoke.provider,
    }
    if intent.baseline_crate is not None:
        data["baseline_crate"] = str(intent.baseline_crate)
    return data


_INTENT_FENCE_RE = re.compile(r"```(?:yaml)?\n(.*?)\n```", re.DOTALL)


def _extract_intent_block(text: str) -> str | None:
    """Return the YAML body if the assistant emitted a ``# AuthorIntent`` section."""
    if _INTENT_HEADER not in text:
        return None
    idx = text.index(_INTENT_HEADER) + len(_INTENT_HEADER)
    tail = text[idx:]
    match = _INTENT_FENCE_RE.search(tail)
    if match is None:
        return tail.strip()
    return match.group(1).strip()


def _parse_intent_block(body: str) -> AuthorIntent:
    """Validate the YAML intent body and freeze it into an :class:`AuthorIntent`."""
    try:
        data = yaml.safe_load(body)
    except yaml.YAMLError as e:
        msg = f"AuthorIntent YAML parse failure: {e}"
        raise DialogError(msg) from None
    if not isinstance(data, dict):
        raise DialogError("AuthorIntent body must be a YAML mapping")

    name = _require_name(data.get("name"))
    description = _require_str(data, "description")
    mechanism = _require_str(data, "mechanism_summary")
    param_sketch = data.get("param_schema_sketch", {})
    if not isinstance(param_sketch, dict):
        raise DialogError("`param_schema_sketch` must be a mapping")
    smoke_raw = data.get("smoke_spec")
    if not isinstance(smoke_raw, dict):
        raise DialogError("`smoke_spec` must be a mapping")
    smoke = SmokeSpec.model_validate(smoke_raw)
    experiment = data.get("experiment_spec")
    if experiment is not None and not isinstance(experiment, dict):
        raise DialogError("`experiment_spec` must be a mapping or omitted")
    return AuthorIntent(
        name=name,
        description=description.strip(),
        mechanism_summary=mechanism.strip(),
        param_schema_sketch=dict(param_sketch),
        smoke_spec=smoke,
        experiment_spec=dict(experiment) if isinstance(experiment, dict) else None,
        baseline_crate=None,
    )


def _maybe_attach_baseline(
    intent: AuthorIntent,
    crates_dir: Path,
    transcript: list[dict[str, str]],
    ask_user: Callable[[str], str],
    write_user: Callable[[str], None],
) -> AuthorIntent:
    """Attach the existing crate to the intent if the proposed name collides."""
    crate_path = crate_dir_for(crates_dir, intent.name)
    if not _is_baseline_crate(crate_path):
        return intent

    write_user(
        f"Crate `{intent.name}-strategy/` already exists at {crate_path}. "
        f"Edit existing crate, or pick a different name?"
    )
    reply = ask_user("[edit/rename] > ").strip().lower()
    if reply.startswith("edit") or reply in {"e", "y", "yes"}:
        return replace(intent, baseline_crate=crate_path)
    # Caller asked to rename — re-enter dialog with that context.
    transcript.append(
        {
            "role": "user",
            "content": (
                f"The proposed name `{intent.name}` collides with an existing crate. "
                "Pick a different name and re-emit the AuthorIntent block."
            ),
        }
    )
    raise _RenameRequestedError(intent.name)


class _RenameRequestedError(Exception):
    """Internal signal: dialog must continue with a different name."""

    def __init__(self, conflicting_name: str) -> None:
        super().__init__(conflicting_name)
        self.conflicting_name = conflicting_name


def _require_name(value: object) -> str:
    if not isinstance(value, str):
        raise DialogError("`name` must be a string")
    name = value.strip()
    if not _NAME_RE.fullmatch(name):
        raise DialogError(
            f"`name` must match `[a-z][a-z0-9_-]{{0,39}}$`; got {name!r}",
        )
    return name


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DialogError(f"`{key}` must be a non-empty string")
    return value


# ---------------------------------------------------------------------------
# author_strategy
# ---------------------------------------------------------------------------


class AuthorBudgetExhaustedError(RuntimeError):
    """Repair budget exhausted; control should return to the dialog."""

    def __init__(
        self, stage: str, attempts: int, last_feedback: str, attempts_trail: list[str] | None = None
    ) -> None:
        super().__init__(
            f"author {stage} stage exhausted repair budget after {attempts} attempts; "
            f"last feedback: {last_feedback[:200]}"
        )
        self.stage = stage
        self.attempts = attempts
        self.last_feedback = last_feedback
        self.attempts_trail = list(attempts_trail or [])


@dataclass(frozen=True)
class RepairMenuChoice:
    """Operator's response to the repair-exhaustion menu."""

    kind: Literal["suggest_alternative", "extend_budget", "edit_decision", "abort"]
    payload: dict[str, Any]


RepairMenuPrompt = Callable[[AuthorBudgetExhaustedError], RepairMenuChoice]
"""Callable that asks the operator how to proceed after exhaustion.

Returns a :class:`RepairMenuChoice`. The dialog orchestrator inspects
``kind`` and applies the corresponding amendment or termination.
"""


def author_strategy(intent: AuthorIntent, *, deps: AuthorDeps) -> AuthoredStrategy:  # noqa: PLR0915 — emit/build/smoke validation has many discrete substep events
    """Drive the emit / build / smoke loop against ``intent``.

    Library seam: the dialog stage is optional; programmatic callers
    (e.g. the hypothesis loop's future ``generate`` rewrite) build an
    :class:`AuthorIntent` directly and call this function.
    """
    import time  # noqa: PLC0415 — used only for build-step timing

    from .prompts_author import build_emit_prompt  # noqa: PLC0415 — avoid import cycle

    crate_path = crate_dir_for(deps.crates_dir, intent.name)
    crate_path.mkdir(parents=True, exist_ok=True)

    sink = deps.event_sink
    last_artifact_hash = ""
    attempt_idx = -1
    budget = deps.repair_config_emit.k_repair

    def emit(feedback: str) -> str:
        nonlocal attempt_idx
        attempt_idx += 1
        sink(RepairAttemptStarted(attempt=attempt_idx, budget=budget))
        prompt = build_emit_prompt(intent=intent, feedback=feedback, crates_dir=deps.crates_dir)
        return deps.reasoning_client.emit_files(system=prompt.system, user=prompt.user)

    def _complete(outcome: ValidationOutcome) -> ValidationOutcome:
        sink(RepairAttemptCompleted(attempt=attempt_idx, outcome=outcome.kind or "ok"))
        return outcome

    def validate(response: str) -> ValidationOutcome:  # noqa: PLR0911 — distinct early-rejects keep the failure taxonomy explicit
        nonlocal last_artifact_hash
        try:
            parsed = parse_stage3(response)
        except ParseError as e:
            return _complete(ValidationOutcome(ok=False, kind="reject_format", feedback=str(e)))
        files = parsed.files
        manifest_text = files.get("Cargo.toml")
        source_text = files.get("src/lib.rs")
        smoke_text = files.get("smoke.toml")
        if manifest_text is None or source_text is None or smoke_text is None:
            missing = [
                p
                for p, present in (
                    ("Cargo.toml", manifest_text is not None),
                    ("src/lib.rs", source_text is not None),
                    ("smoke.toml", smoke_text is not None),
                )
                if not present
            ]
            return _complete(
                ValidationOutcome(
                    ok=False,
                    kind="reject_format",
                    feedback=f"missing required file(s): {missing}",
                )
            )

        _write_files(crate_path, files)
        for rel in files:
            sink(FileWritten(path=rel))

        try:
            manifest = _parse_manifest(manifest_text)
        except ValueError as e:
            return _complete(ValidationOutcome(ok=False, kind="reject_format", feedback=str(e)))

        sink(LintStarted())
        lint = deps.build_pipeline.lint(source_text, manifest)
        sink(LintCompleted(ok=lint.ok))
        if not lint.ok:
            return _complete(
                ValidationOutcome(
                    ok=False,
                    kind="reject_lint",
                    feedback=(
                        f"lint failed; source_violations={lint.source_violations}, "
                        f"manifest_violations={lint.manifest_violations}"
                    ),
                )
            )

        build_args = ("cargo", "build", "-p", f"{intent.name}-strategy")
        sink(CargoBuildStarted(args=build_args))
        build_start = time.monotonic()
        try:
            outcome = deps.build_pipeline.build(source_text, manifest)
        except BuildFailure as e:
            sink(
                CargoBuildCompleted(
                    returncode=1, duration_seconds=time.monotonic() - build_start
                )
            )
            return _complete(
                ValidationOutcome(
                    ok=False, kind=f"reject_build:{e.kind.value}", feedback=e.message
                )
            )
        sink(
            CargoBuildCompleted(
                returncode=0, duration_seconds=time.monotonic() - build_start
            )
        )

        try:
            smoke_spec = _parse_smoke_toml(smoke_text)
        except ValueError as e:
            return _complete(ValidationOutcome(ok=False, kind="reject_format", feedback=str(e)))

        sink(
            SmokeFetchStarted(
                symbol=smoke_spec.symbol,
                start=smoke_spec.start,
                end=smoke_spec.end,
            )
        )
        sink(SmokeFetchCompleted(symbol=smoke_spec.symbol))
        sink(SmokeRunStarted())
        smoke = deps.smoke_runner(Path(outcome.artifact.library_path), smoke_spec)
        sink(
            SmokeRunCompleted(
                ok=smoke.ok,
                trade_count=_trade_count_from_feedback(smoke),
                sanity_trips=_sanity_trips_from_feedback(smoke),
            )
        )
        if not smoke.ok:
            return _complete(
                ValidationOutcome(
                    ok=False,
                    kind="reject_smoke",
                    feedback=smoke.feedback or "smoke failed (no diagnostic)",
                )
            )

        last_artifact_hash = outcome.artifact.key
        return _complete(
            ValidationOutcome(ok=True, parsed={"files": files, "smoke_spec": smoke_spec})
        )

    result = run_stage_with_repair(
        stage=1,
        emit_fn=emit,
        validate_fn=validate,
        config=deps.repair_config_emit,
    )
    if not result.accepted:
        last_feedback = (
            result.attempts[-1].outcome.feedback if result.attempts else "no attempts recorded"
        )
        attempts_trail = [a.outcome.feedback for a in result.attempts]
        raise AuthorBudgetExhaustedError(
            "emit", result.attempts_count, last_feedback, attempts_trail
        )

    _persist_intent(crate_path, intent)
    if intent.experiment_spec is not None:
        _persist_experiment(crate_path, intent.experiment_spec)
    return AuthoredStrategy(
        name=intent.name,
        crate_path=crate_path,
        artifact_hash=last_artifact_hash,
        intent=intent,
    )


def amend_intent_via_llm(
    *,
    intent: AuthorIntent,
    guidance: str,
    failure_trail: str,
    reasoning_client: AuthorReasoningClient,
    scope_field: str | None = None,
) -> AuthorIntent:
    """Ask the LLM to revise ``intent`` per operator guidance.

    Used by :func:`run_author_session` after a repair-budget exhaustion
    when the operator chose option 1 ("suggest alternative approach") or
    option 3 ("edit a specific decision"). The LLM is given the previous
    intent, the failure trail, and the operator's guidance; it returns
    a single ``# AuthorIntent`` block which is parsed back into a frozen
    :class:`AuthorIntent`.
    """
    from .prompts_author import (  # noqa: PLC0415 — avoid import cycle
        build_amend_intent_prompt,
    )

    previous_yaml = _intent_yaml_for_amend(intent)
    system = build_amend_intent_prompt(
        previous_intent_yaml=previous_yaml,
        failure_trail=failure_trail,
        guidance=guidance,
        scope_field=scope_field,
    )
    response = reasoning_client.dialog_turn(
        system=system,
        transcript=[{"role": "user", "content": "Emit the revised AuthorIntent."}],
    )
    block = _extract_intent_block(response)
    if block is None:
        msg = "amendment LLM turn did not emit a `# AuthorIntent` block"
        raise DialogError(msg)
    new_intent = _parse_intent_block(block)
    # Preserve baseline_crate (edit-mode is a property of the run, not the LLM emission)
    return replace(new_intent, baseline_crate=intent.baseline_crate)


def _intent_yaml_for_amend(intent: AuthorIntent) -> str:
    """Serialize ``intent`` as YAML for the amendment prompt."""
    smoke = intent.smoke_spec
    body: dict[str, Any] = {
        "name": intent.name,
        "description": intent.description,
        "mechanism_summary": intent.mechanism_summary,
        "param_schema_sketch": intent.param_schema_sketch,
        "smoke_spec": {
            "symbol": smoke.symbol,
            "resolution": smoke.resolution,
            "start": smoke.start,
            "end": smoke.end,
            "provider": smoke.provider,
        },
    }
    if intent.experiment_spec is not None:
        body["experiment_spec"] = intent.experiment_spec
    return str(yaml.safe_dump(body, sort_keys=False, allow_unicode=True))


def run_author_session(
    intent: AuthorIntent,
    *,
    deps: AuthorDeps,
    reasoning_client: AuthorReasoningClient,
    repair_menu: RepairMenuPrompt,
    write_user: Callable[[str], None] = print,
) -> AuthoredStrategy:
    """Run ``author_strategy`` with repair-budget recovery loop.

    On :class:`AuthorBudgetExhaustedError`, append a ``RepairBudgetExhausted``
    event to the DecisionRecord (if one is open), then present the
    operator menu via ``repair_menu``. The menu's response selects one
    of four paths: amend the intent in NL, extend the repair budget,
    edit a specific decision field, or abort. The crate files on disk
    are left in place across retries so the operator can inspect them.
    """
    current_intent = intent
    current_deps = deps
    while True:
        try:
            return author_strategy(current_intent, deps=current_deps)
        except AuthorBudgetExhaustedError as exc:
            if current_deps.decision_record_path is not None:
                rec = DecisionRecord.open(current_deps.decision_record_path)
                rec.append(
                    RepairBudgetExhausted(
                        timestamp=_now_iso(),
                        stage=exc.stage,
                        attempts=exc.attempts,
                        last_feedback=exc.last_feedback,
                    )
                )
            write_user(
                f"Repair budget exhausted after {exc.attempts} attempts on stage "
                f"`{exc.stage}`. Last feedback:\n{exc.last_feedback}"
            )
            choice = repair_menu(exc)
            if choice.kind == "abort":
                raise
            if choice.kind == "extend_budget":
                new_k_emit = int(
                    choice.payload.get("k_repair_emit", deps.repair_config_emit.k_repair)
                )
                new_k_build = int(
                    choice.payload.get("k_repair_build", deps.repair_config_build.k_repair)
                )
                current_deps = replace(
                    current_deps,
                    repair_config_emit=RepairConfig(k_repair=new_k_emit),
                    repair_config_build=RepairConfig(k_repair=new_k_build),
                )
                continue
            if choice.kind == "suggest_alternative":
                guidance = str(choice.payload.get("guidance", ""))
                current_intent = amend_intent_via_llm(
                    intent=current_intent,
                    guidance=guidance,
                    failure_trail="\n---\n".join(exc.attempts_trail),
                    reasoning_client=reasoning_client,
                )
                continue
            if choice.kind == "edit_decision":
                field_name = str(choice.payload.get("field", ""))
                guidance = str(choice.payload.get("guidance", ""))
                current_intent = amend_intent_via_llm(
                    intent=current_intent,
                    guidance=guidance,
                    failure_trail="\n---\n".join(exc.attempts_trail),
                    reasoning_client=reasoning_client,
                    scope_field=field_name,
                )
                continue
            msg = f"unknown repair-menu choice: {choice.kind!r}"
            raise RuntimeError(msg) from None


def crate_dir_for(crates_dir: Path, name: str) -> Path:
    """Return the conventional crate directory for an intent name."""
    return crates_dir / f"{name}-strategy"


def _trade_count_from_feedback(smoke: SmokeRunResult) -> int:
    """Best-effort extraction of trade count from a :class:`SmokeRunResult`."""
    return _int_field_from_feedback(smoke.feedback, "trades")


def _sanity_trips_from_feedback(smoke: SmokeRunResult) -> int:
    """Best-effort extraction of sanity-trip count from a :class:`SmokeRunResult`."""
    return _int_field_from_feedback(smoke.feedback, "sanity_trips")


def _int_field_from_feedback(feedback: str, field_name: str) -> int:
    """Pull ``<field_name>=<int>`` from a smoke feedback string if present."""
    match = re.search(rf"{re.escape(field_name)}\s*=\s*(\d+)", feedback)
    return int(match.group(1)) if match else 0


def _is_baseline_crate(crate_path: Path) -> bool:
    """Return True when ``crate_path`` holds a previously-authored crate.

    A bare ``.author/`` directory (created by the in-flight dialog to
    hold its decision log) is not a baseline; we only treat the path as
    one when actual crate files are present.
    """
    return (crate_path / "Cargo.toml").exists() or (crate_path / "src" / "lib.rs").exists()


def _write_files(crate_path: Path, files: dict[str, str]) -> None:
    """Write all emitted files under ``crate_path``.

    The ``Cargo.toml`` body is normalized so the on-disk crate compiles
    standalone inside the workspace: the LLM occasionally emits
    ``engine-rt = "*"`` (a registry-style dep) instead of the path dep
    used in the exemplars, which makes ``cargo check --workspace`` fail
    even though the build-pipeline sandbox overrides the manifest at
    build time. Rewriting on the write boundary keeps the crate
    inspectable and workspace-clean regardless of LLM drift.
    """
    for rel, body in files.items():
        normalized = _normalize_cargo_toml(body) if rel == "Cargo.toml" else body
        target = crate_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(normalized, encoding="utf-8")


_CARGO_ENGINE_RT_DEP_RE = re.compile(
    r'^engine-rt\s*=.*$',
    re.MULTILINE,
)


def _normalize_cargo_toml(text: str) -> str:
    """Rewrite an LLM-emitted ``Cargo.toml`` so ``engine-rt`` is a path dep.

    The build pipeline's sandboxed Cargo.toml already injects the path
    dep correctly. Normalizing the on-disk Cargo.toml ensures the
    persisted crate also resolves the dep inside the workspace.
    """
    replacement = 'engine-rt = { path = "../engine-rt" }'
    if _CARGO_ENGINE_RT_DEP_RE.search(text):
        return _CARGO_ENGINE_RT_DEP_RE.sub(replacement, text, count=1)
    # No engine-rt dep at all — append one under [dependencies].
    if "[dependencies]" in text:
        return text.replace("[dependencies]", f"[dependencies]\n{replacement}", 1)
    return text.rstrip() + f"\n\n[dependencies]\n{replacement}\n"


_MANIFEST_DEP_RE = re.compile(
    r'^([a-zA-Z0-9_-]+)\s*=\s*(?:"([^"]+)"|\{[^}]*?(?:version\s*=\s*"([^"]+)"|workspace\s*=\s*true)[^}]*?\})\s*$',
    re.MULTILINE,
)


def _parse_manifest(text: str) -> StrategyManifest:
    """Lift the LLM-emitted ``Cargo.toml`` into a :class:`StrategyManifest`."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        msg = f"Cargo.toml is not valid TOML: {e}"
        raise ValueError(msg) from None
    pkg = data.get("package")
    if not isinstance(pkg, dict):
        raise ValueError("Cargo.toml missing `[package]` table")
    name = pkg.get("name")
    version = pkg.get("version", "0.1.0")
    if not isinstance(name, str) or not name:
        raise ValueError("Cargo.toml `[package].name` must be a string")

    def _deps(section: dict[str, Any] | None) -> list[ManifestDep]:
        if not isinstance(section, dict):
            return []
        out: list[ManifestDep] = []
        for dep_name, spec in section.items():
            if isinstance(spec, str):
                req = spec
            elif isinstance(spec, dict):
                req = spec.get("version", "*")
            else:
                req = "*"
            out.append(ManifestDep(name=str(dep_name), req=str(req)))
        return out

    return StrategyManifest(
        name=name,
        version=str(version),
        dependencies=_deps(data.get("dependencies")),
        dev_dependencies=_deps(data.get("dev-dependencies")),
        build_dependencies=_deps(data.get("build-dependencies")),
    )


def _parse_smoke_toml(text: str) -> SmokeSpec:
    """Parse ``smoke.toml`` into a frozen :class:`SmokeSpec`."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        msg = f"smoke.toml is not valid TOML: {e}"
        raise ValueError(msg) from None
    return SmokeSpec.model_validate(data)


def _persist_intent(crate_path: Path, intent: AuthorIntent) -> None:
    """Persist the structured intent as TOML alongside the source.

    The file is the authoritative on-disk record. The format is hand-
    written rather than dumped via a third-party library to keep the
    dependency surface minimal.
    """
    target = crate_path / "intent.toml"
    target.write_text(_intent_to_toml(intent), encoding="utf-8")


def _persist_experiment(crate_path: Path, spec: dict[str, Any]) -> None:
    """Persist the full-batch experiment spec as ``experiment.yaml``."""
    target = crate_path / "experiment.yaml"
    target.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _intent_to_toml(intent: AuthorIntent) -> str:
    """Hand-roll a TOML emission for an :class:`AuthorIntent`.

    The schema is small (string scalars + two nested tables) so a manual
    writer is cheaper than pulling in ``tomli_w``. Round-trips through
    :func:`load_intent_toml` are covered by ``test_author``.
    """
    lines: list[str] = []
    lines.append(f"name = {_toml_str(intent.name)}")
    lines.append(f"description = {_toml_multi(intent.description)}")
    lines.append(f"mechanism_summary = {_toml_multi(intent.mechanism_summary)}")
    if intent.baseline_crate is not None:
        lines.append(f"baseline_crate = {_toml_str(str(intent.baseline_crate))}")
    lines.append("")
    lines.append("[smoke_spec]")
    smoke = intent.smoke_spec
    lines.append(f"symbol = {_toml_str(smoke.symbol)}")
    lines.append(f"resolution = {_toml_str(smoke.resolution)}")
    lines.append(f"start = {_toml_str(smoke.start)}")
    lines.append(f"end = {_toml_str(smoke.end)}")
    lines.append(f"provider = {_toml_str(smoke.provider)}")
    lines.append("")
    lines.append("[param_schema_sketch]")
    lines.append(f"_json = {_toml_literal(json.dumps(intent.param_schema_sketch, sort_keys=True))}")
    if intent.experiment_spec is not None:
        lines.append("")
        lines.append("[experiment_spec]")
        lines.append(
            f"_yaml = {_toml_literal(yaml.safe_dump(intent.experiment_spec, sort_keys=False))}"
        )
    return "\n".join(lines) + "\n"


def load_intent_toml(crate_path: Path) -> AuthorIntent:
    """Round-trip helper: load a persisted ``intent.toml``."""
    text = (crate_path / "intent.toml").read_text(encoding="utf-8")
    data = tomllib.loads(text)
    smoke = SmokeSpec.model_validate(data["smoke_spec"])
    raw_schema = data.get("param_schema_sketch", {})
    param_schema_sketch = (
        json.loads(raw_schema["_json"])
        if isinstance(raw_schema, dict) and "_json" in raw_schema
        else dict(raw_schema)
    )
    experiment_raw = data.get("experiment_spec")
    experiment_spec: dict[str, Any] | None = None
    if isinstance(experiment_raw, dict) and "_yaml" in experiment_raw:
        loaded = yaml.safe_load(experiment_raw["_yaml"])
        experiment_spec = loaded if isinstance(loaded, dict) else None
    baseline = data.get("baseline_crate")
    return AuthorIntent(
        name=str(data["name"]),
        description=str(data["description"]),
        mechanism_summary=str(data["mechanism_summary"]),
        param_schema_sketch=param_schema_sketch,
        smoke_spec=smoke,
        experiment_spec=experiment_spec,
        baseline_crate=Path(str(baseline)) if isinstance(baseline, str) else None,
    )


def _toml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_multi(value: str) -> str:
    # Multi-line content uses TOML's triple-double-quoted form. TOML
    # trims the opening newline, so a trailing newline appears in the
    # decoded value only when the source carries one.
    if "\n" not in value:
        return _toml_str(value)
    escaped = value.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return f'"""\n{escaped}"""'


def _toml_literal(value: str) -> str:
    # TOML literal triple-quoted strings preserve content verbatim with
    # no escape processing — ideal for embedded JSON or YAML payloads.
    # Triple-single-quote sequences in the value are illegal in this
    # form; fall back to the escaped basic-string in that rare case.
    if "'''" in value:
        return _toml_multi(value)
    return f"'''\n{value}'''"


__all__ = [
    "AuthorBudgetExhaustedError",
    "AuthorDeps",
    "AuthorIntent",
    "AuthorReasoningClient",
    "AuthoredStrategy",
    "DialogError",
    "RepairMenuChoice",
    "RepairMenuPrompt",
    "SmokeRunResult",
    "SmokeRunner",
    "SmokeSpec",
    "amend_intent_via_llm",
    "author_strategy",
    "crate_dir_for",
    "load_intent_toml",
    "run_author_session",
    "run_intent_dialog",
]
