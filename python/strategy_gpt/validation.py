"""Stage emission validators for the hypothesis-loop repair loop.

Each validator converts a raw markdown emission into a
:class:`strategy_gpt.repair.ValidationOutcome`. Validation failures are
classified into the structured reject taxonomy (``reject_format``,
``reject_build``, ``reject_lint``, ``reject_schema``, ``reject_smoke``)
so the repair loop can synthesize targeted feedback for the next
attempt.

Wiring (`hypothesis-loop::repair-loop-per-stage`, task 2.9):

- ``validate_stage1`` — markdown parse only.
- ``validate_stage2`` — markdown parse + metric allow-list check (the
  parser already covers schema-level validation; this is the entry
  point the repair loop hits).
- ``validate_stage3`` — markdown parse, lint via the build-pipeline,
  full build, and a declared-param-schema cross-check against the
  locked stage-2 ``param_intent``. Each failure mode reports its
  ``reject_*`` kind and a feedback string that names the offending file
  or schema mismatch so the next repair attempt can act on it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Protocol

from .build_pipeline import (
    BuildErrorKind,
    BuildFailure,
    LintReport,
    ManifestDep,
    StrategyManifest,
)
from .markdown_io import (
    ParseError,
    Stage1Idea,
    Stage2Commitments,
    Stage3Files,
    parse_stage1,
    parse_stage2,
    parse_stage3,
)
from .repair import ValidationOutcome

# ---------------------------------------------------------------------------
# Stage 1 & 2 — markdown-only validation
# ---------------------------------------------------------------------------


def validate_stage1(response: str) -> ValidationOutcome:
    """Parse a stage-1 emission. Failure → ``reject_format``."""
    try:
        idea = parse_stage1(response)
    except ParseError as e:
        return ValidationOutcome(ok=False, kind="reject_format", feedback=str(e))
    return ValidationOutcome(ok=True, parsed=idea)


def validate_stage2(
    response: str,
    *,
    allowed_metrics: frozenset[str] | None = None,
) -> ValidationOutcome:
    """Parse a stage-2 emission. Failure → ``reject_format``.

    Metric-name validation already happens inside the parser when
    ``allowed_metrics`` is supplied; the outcome surfaces it here as
    ``reject_format`` (the parser uses the same reject kind for any
    grammar / schema mismatch — the kind taxonomy distinguishes
    *categories* of repair, not parser line numbers).
    """
    try:
        commitments = parse_stage2(response, allowed_metrics=allowed_metrics)
    except ParseError as e:
        return ValidationOutcome(ok=False, kind="reject_format", feedback=str(e))
    return ValidationOutcome(ok=True, parsed=commitments)


# ---------------------------------------------------------------------------
# Stage 3 — markdown parse + build pipeline
# ---------------------------------------------------------------------------


class _BuildPipelineLike(Protocol):
    """Subset of :class:`strategy_gpt.build_pipeline.BuildPipeline` used here.

    The repair-loop tests inject stubs that record calls without going
    through the real cargo build. Declared structurally so the test
    seam is explicit.
    """

    def lint(self, source: str, manifest: StrategyManifest) -> LintReport: ...

    def build(self, source: str, manifest: StrategyManifest) -> object: ...


def _extract_main_source(files: dict[str, str]) -> str | None:
    """The build-pipeline `build(source, manifest)` API takes a single
    source string today; pull it from ``src/lib.rs``. Returns ``None``
    if the candidate didn't emit a ``src/lib.rs`` — caller treats as
    ``reject_format``.
    """
    return files.get("src/lib.rs")


def _extract_manifest_text(files: dict[str, str]) -> str | None:
    return files.get("Cargo.toml")


def _parse_cargo_toml(text: str) -> tuple[StrategyManifest, str | None]:
    """Tiny pragmatic parser for the strategy crate's Cargo.toml.

    The Rust side validates the full manifest in the build-pipeline.
    Here we extract just enough to build the Python ``StrategyManifest``
    shape the FFI surface expects. Returns ``(manifest, error)``; when
    ``error`` is non-None, the caller emits ``reject_format``.

    Why a custom parser instead of pulling in `tomllib`: the input is
    LLM-emitted and may not be well-formed TOML. We want a precise
    feedback string ("missing `[package]` table") rather than a
    PythonError trace. `tomllib` falls back to ``reject_format`` with
    its own error message when our parser cannot find what it needs.
    """
    try:
        import tomllib  # noqa: PLC0415 — stdlib import deferred so non-cargo paths don't pay

        data = tomllib.loads(text)
    except Exception as e:
        return (
            StrategyManifest(name="invalid", version="0.0.0"),
            f"Cargo.toml is not valid TOML: {e}",
        )

    pkg = data.get("package")
    if not isinstance(pkg, dict):
        return (
            StrategyManifest(name="invalid", version="0.0.0"),
            "Cargo.toml is missing the [package] table",
        )
    name = pkg.get("name")
    version = pkg.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        return (
            StrategyManifest(name="invalid", version="0.0.0"),
            "[package].name and [package].version must be strings",
        )

    deps = data.get("dependencies") or {}
    deps_list = []
    if isinstance(deps, dict):
        for dname, dspec in deps.items():
            if isinstance(dspec, str):
                req = dspec
            elif isinstance(dspec, dict):
                # Accept either {version = "..."} or path-style entries.
                raw_req = dspec.get("version")
                req = raw_req if isinstance(raw_req, str) else "*"
            else:
                req = "*"
            deps_list.append(ManifestDep(name=dname, req=req))

    manifest = StrategyManifest(name=name, version=version, dependencies=deps_list)
    return manifest, None


def _check_param_intent_against_schema(
    param_intent: dict[str, object],
    params_schema_text: str,
) -> str | None:
    """Cross-check the stage-2 ``param_intent`` against the candidate's
    declared ``params_schema.json``. Returns an error string on
    mismatch (``reject_schema``) or ``None`` when consistent.

    Today's check: every ``added`` parameter name must appear in the
    candidate's declared schema, and every ``removed`` name MUST NOT.
    Bounds and kinds are NOT cross-checked here because the schema
    declares its own bounds and the build-pipeline's `parse_params_schema`
    already validates them; future refinement can tighten the check.
    """
    try:
        schema = json.loads(params_schema_text)
    except json.JSONDecodeError as e:
        return f"params_schema.json is not valid JSON: {e}"
    declared_names: set[str] = set()
    for entry in schema.get("params", []) or []:
        if isinstance(entry, dict):
            n = entry.get("name")
            if isinstance(n, str):
                declared_names.add(n)

    raw_added = param_intent.get("added") or []
    added_names: set[str] = set()
    if isinstance(raw_added, list):
        for entry in raw_added:
            if isinstance(entry, dict):
                ename = entry.get("name")
                if isinstance(ename, str):
                    added_names.add(ename)
    missing_added = added_names - declared_names
    if missing_added:
        return (
            "params_schema.json is missing parameter(s) declared in stage-2 "
            f"`added`: {sorted(missing_added)}"
        )

    raw_removed = param_intent.get("removed") or []
    removed: set[str] = set()
    if isinstance(raw_removed, list):
        removed = {n for n in raw_removed if isinstance(n, str)}
    leaked_removed = removed & declared_names
    if leaked_removed:
        return (
            "params_schema.json still declares parameter(s) listed as "
            f"`removed` in stage-2: {sorted(leaked_removed)}"
        )
    return None


def _format_lint_feedback(report: LintReport) -> str:
    lines: list[str] = ["build-pipeline lint failed:"]
    for v in report.source_violations:
        lines.append(f"  source: {v}")
    for v in report.manifest_violations:
        lines.append(f"  manifest: {v}")
    return "\n".join(lines)


def _format_build_feedback(err: BuildFailure, *, max_errors: int = 3) -> str:
    """Format a build failure as repair feedback. Truncates noisy
    rustc errors to the first ``max_errors`` lines so the next prompt
    stays focused.
    """
    text = err.message
    lines = text.splitlines()
    # Trim to first `max_errors` `error[E...]` blocks heuristically.
    out: list[str] = [f"build-pipeline {err.kind.value} failed:"]
    seen_errors = 0
    for line in lines:
        if line.startswith("error"):
            seen_errors += 1
            if seen_errors > max_errors:
                out.append(f"  ... ({len(lines) - len(out)} more lines elided)")
                break
        out.append(f"  {line}")
    return "\n".join(out)


_BUILD_REJECT_KIND_BY_BUILD_ERROR = {
    BuildErrorKind.SOURCE_LINT: "reject_lint",
    BuildErrorKind.MANIFEST_LINT: "reject_lint",
    BuildErrorKind.WHITELIST: "reject_deps",
    BuildErrorKind.IO: "reject_build",
    BuildErrorKind.CARGO: "reject_build",
    BuildErrorKind.ARTIFACT_CACHE: "reject_build",
    BuildErrorKind.MIGRATION: "reject_build",
}


def _build_kind_to_reject_kind(kind: BuildErrorKind) -> str:
    return _BUILD_REJECT_KIND_BY_BUILD_ERROR.get(kind, "reject_build")


def validate_stage3(  # noqa: PLR0911 — strict-validator with one branch per reject kind
    response: str,
    *,
    pipeline: _BuildPipelineLike,
    stage2_param_intent: dict[str, object] | None = None,
    required_files: Sequence[str] = ("Cargo.toml", "src/lib.rs", "params_schema.json"),
) -> ValidationOutcome:
    """Validate a stage-3 emission end-to-end.

    Steps (each failure short-circuits with a reject kind + feedback):

    1. **markdown parse** — :func:`parse_stage3`. Failure →
       ``reject_format``.
    2. **required-files check** — at minimum ``Cargo.toml``,
       ``src/lib.rs``, ``params_schema.json`` must be present.
    3. **manifest extraction** — parse Cargo.toml as TOML, project to
       :class:`StrategyManifest`. Failure → ``reject_format``.
    4. **schema cross-check** — `param_intent.added` ⊆ declared schema
       names, `param_intent.removed` ∩ declared names = ∅. Failure →
       ``reject_schema``.
    5. **lint + build** via the pipeline. Maps each
       :class:`BuildErrorKind` to a structural reject kind.

    Multi-file emissions beyond ``src/lib.rs`` are accepted by the
    parser but the current build pipeline only consumes a single
    source string; helper modules under ``src/`` are surfaced via
    feedback so the LLM either inlines or re-emits when the pipeline
    grows multi-file support.
    """
    try:
        files = parse_stage3(response)
    except ParseError as e:
        return ValidationOutcome(ok=False, kind="reject_format", feedback=str(e))

    for required in required_files:
        if required not in files.files:
            return ValidationOutcome(
                ok=False,
                kind="reject_format",
                feedback=f"stage-3 emission missing required file `{required}`",
            )

    src = _extract_main_source(files.files)
    manifest_text = _extract_manifest_text(files.files)
    params_schema_text = files.files.get("params_schema.json")
    if src is None or manifest_text is None or params_schema_text is None:
        return ValidationOutcome(
            ok=False,
            kind="reject_format",
            feedback="stage-3 missing one of `src/lib.rs`, `Cargo.toml`, `params_schema.json`",
        )

    manifest, manifest_err = _parse_cargo_toml(manifest_text)
    if manifest_err is not None:
        return ValidationOutcome(ok=False, kind="reject_format", feedback=manifest_err)

    if stage2_param_intent is not None:
        schema_err = _check_param_intent_against_schema(stage2_param_intent, params_schema_text)
        if schema_err is not None:
            return ValidationOutcome(ok=False, kind="reject_schema", feedback=schema_err)

    # Surface extra source files as informational feedback in the parsed
    # payload so the orchestrator can warn/log. They are NOT a hard reject
    # today; the pipeline only consumes src/lib.rs.
    extra_src = sorted(p for p in files.files if p.startswith("src/") and p != "src/lib.rs")

    lint_report = pipeline.lint(src, manifest)
    if not lint_report.ok:
        return ValidationOutcome(
            ok=False,
            kind="reject_lint",
            feedback=_format_lint_feedback(lint_report),
        )

    try:
        outcome = pipeline.build(src, manifest)
    except BuildFailure as e:
        return ValidationOutcome(
            ok=False,
            kind=_build_kind_to_reject_kind(e.kind),
            feedback=_format_build_feedback(e),
        )

    return ValidationOutcome(
        ok=True,
        parsed={
            "files": files,
            "build_outcome": outcome,
            "extra_src_files": extra_src,
        },
    )


__all__ = [
    "Stage1Idea",
    "Stage2Commitments",
    "Stage3Files",
    "validate_stage1",
    "validate_stage2",
    "validate_stage3",
]
