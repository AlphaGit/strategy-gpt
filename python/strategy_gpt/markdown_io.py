"""Strict markdown parser and serializer for stage-1/2/3 LLM emissions.

The hypothesis loop's reasoning models emit each stage as markdown (not
JSON) — code-fenced files reliably encode whole Rust source without the
nested-quoting failures JSON exhibits on real candidate emissions. This
module defines the on-the-wire contract and a hard-rejecting parser; any
malformed or missing section surfaces as a :class:`ParseError` that
identifies which section failed so the repair loop can synthesize
targeted feedback.

Contract summary (full grammar in
``hypothesis-loop::markdown-emit-and-parse-contract``):

- Stage 1 — idea / rationale / confidence / side effects::

      # Idea
      candidate_name: <slug>
      rationale: |
        <≤500-char free-form>
      expected_lift_confidence: <0.0..1.0>
      expected_side_effects:
        - <bullet>
        - <bullet>

- Stage 2 — falsification + param intent::

      # Falsification
      ```yaml
      primary:
        metric: <name>
        direction: gt | gte | lt | lte
        delta_vs_baseline: <float>
        scope:
          kind: aggregate | regime | fold | window
          regime: <label>?
          fold: <int>?
          window_start: <ISO8601>?
          window_end: <ISO8601>?
      guard_constraints:
        - { metric: <name>, direction: lte, delta_vs_baseline: <float> }
        - { metric: <name>, direction: gte, factor: <float> }
      ```

      # ParamIntent
      ```yaml
      added:
        - { name: <name>, kind: f64|i64|bool|string,
            min: <float>?, max: <float>?, default: <value> }
      kept: [ <name>, <name> ]
      removed: [ <name> ]
      ```

- Stage 3 — files map::

      ## src/lib.rs
      ```rust
      <file content>
      ```

      ## DELETE: src/old_module.rs

The parser is intentionally permissive about ordering inside each stage
(sections may appear in any order) but strict about every required key
and every YAML schema mismatch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParseError(Exception):
    """Structured parse failure.

    ``section`` identifies the offending markdown header (e.g.
    ``"# Idea"``, ``"## src/lib.rs"``, or the empty string when the
    failure is at top-level). ``rationale`` is the human-readable
    message the repair loop uses to synthesize feedback.
    """

    section: str
    rationale: str

    def __str__(self) -> str:
        if self.section:
            return f"{self.section}: {self.rationale}"
        return self.rationale


# ---------------------------------------------------------------------------
# Stage 1 — idea
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stage1Idea:
    candidate_name: str
    rationale: str
    expected_lift_confidence: float
    expected_side_effects: list[str]


_RATIONALE_MAX = 500


def parse_stage1(text: str) -> Stage1Idea:
    """Parse a stage-1 emission.

    The emission is a single ``# Idea`` section whose body is YAML
    (under the convention that ``candidate_name`` / ``rationale`` /
    ``expected_lift_confidence`` / ``expected_side_effects`` keys are
    required). Multi-paragraph rationales are tolerated and silently
    truncated at 500 characters; the full text is preserved by the
    caller via the response-blob storage.
    """
    body = _require_single_section(text, header="Idea")
    data = _load_yaml(body, section="# Idea")
    if not isinstance(data, dict):
        msg = (
            "expected a YAML mapping with `candidate_name`, `rationale`, "
            "`expected_lift_confidence`, `expected_side_effects`"
        )
        raise ParseError(section="# Idea", rationale=msg)

    name = _require_str(data, "candidate_name", section="# Idea")
    rationale = _require_str(data, "rationale", section="# Idea")[:_RATIONALE_MAX]
    confidence = _require_float(data, "expected_lift_confidence", section="# Idea")
    if not 0.0 <= confidence <= 1.0:
        msg = f"`expected_lift_confidence` must be in [0.0, 1.0], got {confidence}"
        raise ParseError(section="# Idea", rationale=msg)
    side_effects = data.get("expected_side_effects", []) or []
    if not isinstance(side_effects, list) or not all(isinstance(s, str) for s in side_effects):
        msg = "`expected_side_effects` must be a list of strings"
        raise ParseError(section="# Idea", rationale=msg)

    return Stage1Idea(
        candidate_name=name.strip(),
        rationale=rationale.strip(),
        expected_lift_confidence=confidence,
        expected_side_effects=[s.strip() for s in side_effects],
    )


def serialize_stage1(idea: Stage1Idea) -> str:
    body = {
        "candidate_name": idea.candidate_name,
        "rationale": idea.rationale,
        "expected_lift_confidence": idea.expected_lift_confidence,
        "expected_side_effects": list(idea.expected_side_effects),
    }
    yaml_block = yaml.safe_dump(body, sort_keys=False, allow_unicode=True).strip()
    return f"# Idea\n\n{yaml_block}\n"


# ---------------------------------------------------------------------------
# Stage 2 — commitments (falsification + param_intent)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stage2Commitments:
    falsification: dict[str, Any]
    param_intent: dict[str, Any]


_ALLOWED_DIRECTIONS = frozenset({"gt", "gte", "lt", "lte", "eq"})
_ALLOWED_SCOPE_KINDS = frozenset({"aggregate", "regime", "fold", "window"})
_ALLOWED_PARAM_KINDS = frozenset({"f64", "i64", "bool", "string"})


def parse_stage2(
    text: str,
    *,
    allowed_metrics: frozenset[str] | None = None,
) -> Stage2Commitments:
    """Parse a stage-2 emission.

    Two sections are required: ``# Falsification`` and ``# ParamIntent``.
    Each section carries a single fenced YAML block.

    ``allowed_metrics``, when supplied, restricts the primary/guard
    ``metric`` field to that set; the parser surfaces the offending name
    on mismatch so the repair loop can include the canonical list.
    """
    sections = _split_h1_sections(text)
    if "Falsification" not in sections or "ParamIntent" not in sections:
        missing = [k for k in ("Falsification", "ParamIntent") if k not in sections]
        msg = f"missing required H1 section(s): {', '.join(missing)}"
        raise ParseError(section="", rationale=msg)

    falsification = _load_yaml_block(sections["Falsification"], section="# Falsification")
    if not isinstance(falsification, dict):
        raise ParseError(
            section="# Falsification",
            rationale="expected a YAML mapping with `primary` and optional `guard_constraints`",
        )
    _validate_falsification(falsification, allowed_metrics)

    param_intent = _load_yaml_block(sections["ParamIntent"], section="# ParamIntent")
    if not isinstance(param_intent, dict):
        raise ParseError(
            section="# ParamIntent",
            rationale="expected a YAML mapping with `added`, `kept`, `removed`",
        )
    _validate_param_intent(param_intent)

    return Stage2Commitments(falsification=falsification, param_intent=param_intent)


def serialize_stage2(commitments: Stage2Commitments) -> str:
    fal_yaml = yaml.safe_dump(commitments.falsification, sort_keys=False).strip()
    pi_yaml = yaml.safe_dump(commitments.param_intent, sort_keys=False).strip()
    return (
        f"# Falsification\n\n```yaml\n{fal_yaml}\n```\n\n# ParamIntent\n\n```yaml\n{pi_yaml}\n```\n"
    )


def _validate_falsification(body: dict[str, Any], allowed_metrics: frozenset[str] | None) -> None:
    section = "# Falsification"
    primary = body.get("primary")
    if not isinstance(primary, dict):
        raise ParseError(section=section, rationale="`primary` is required and must be a mapping")
    metric = _require_str(primary, "metric", section=section)
    if allowed_metrics is not None and metric not in allowed_metrics:
        allowed = ", ".join(sorted(allowed_metrics))
        raise ParseError(
            section=section,
            rationale=f"unknown metric `{metric}`; allowed: {allowed}",
        )
    direction = _require_str(primary, "direction", section=section)
    if direction not in _ALLOWED_DIRECTIONS:
        raise ParseError(
            section=section,
            rationale=f"unknown direction `{direction}`; allowed: {sorted(_ALLOWED_DIRECTIONS)}",
        )
    _require_float(primary, "delta_vs_baseline", section=section)
    scope = primary.get("scope")
    if scope is not None:
        if not isinstance(scope, dict):
            raise ParseError(section=section, rationale="`primary.scope` must be a mapping")
        kind = scope.get("kind", "aggregate")
        if kind not in _ALLOWED_SCOPE_KINDS:
            raise ParseError(
                section=section,
                rationale=f"unknown scope kind `{kind}`; allowed: {sorted(_ALLOWED_SCOPE_KINDS)}",
            )

    guards = body.get("guard_constraints", []) or []
    if not isinstance(guards, list):
        raise ParseError(section=section, rationale="`guard_constraints` must be a list")
    for i, guard in enumerate(guards):
        if not isinstance(guard, dict):
            raise ParseError(section=section, rationale=f"guard_constraints[{i}] must be a mapping")
        gmetric = _require_str(guard, "metric", section=section)
        if allowed_metrics is not None and gmetric not in allowed_metrics:
            allowed = ", ".join(sorted(allowed_metrics))
            raise ParseError(
                section=section,
                rationale=f"guard_constraints[{i}].metric `{gmetric}` not in {allowed}",
            )
        direction = _require_str(guard, "direction", section=section)
        if direction not in _ALLOWED_DIRECTIONS:
            raise ParseError(
                section=section,
                rationale=f"guard_constraints[{i}].direction `{direction}` invalid",
            )
        if "delta_vs_baseline" not in guard and "factor" not in guard:
            raise ParseError(
                section=section,
                rationale=f"guard_constraints[{i}] must declare `delta_vs_baseline` or `factor`",
            )


def _validate_param_intent(body: dict[str, Any]) -> None:
    section = "# ParamIntent"
    added = body.get("added", []) or []
    if not isinstance(added, list):
        raise ParseError(section=section, rationale="`added` must be a list")
    names: set[str] = set()
    for i, entry in enumerate(added):
        if not isinstance(entry, dict):
            raise ParseError(section=section, rationale=f"added[{i}] must be a mapping")
        name = _require_str(entry, "name", section=section)
        if name in names:
            raise ParseError(section=section, rationale=f"added[{i}].name `{name}` duplicates")
        names.add(name)
        kind = _require_str(entry, "kind", section=section)
        if kind not in _ALLOWED_PARAM_KINDS:
            raise ParseError(
                section=section,
                rationale=f"added[{i}].kind `{kind}` not in {sorted(_ALLOWED_PARAM_KINDS)}",
            )
        if kind in {"f64", "i64"} and ("min" not in entry or "max" not in entry):
            raise ParseError(
                section=section,
                rationale=f"added[{i}] numeric kind requires `min` and `max`",
            )
        if "default" not in entry:
            raise ParseError(section=section, rationale=f"added[{i}] missing `default`")
    for key in ("kept", "removed"):
        v = body.get(key, []) or []
        if not isinstance(v, list) or not all(isinstance(s, str) for s in v):
            raise ParseError(
                section=section, rationale=f"`{key}` must be a list of parameter names"
            )


# ---------------------------------------------------------------------------
# Stage 3 — files map
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Stage3Files:
    files: dict[str, str] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)


_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
_FENCE_OPEN_RE = re.compile(r"^```(?:[A-Za-z0-9_+-]*)\s*$")
_FENCE_CLOSE_RE = re.compile(r"^```\s*$")


def parse_stage3(text: str) -> Stage3Files:  # noqa: PLR0912 — strict parser branches by design
    """Parse a stage-3 files map emission.

    Each file is encoded as a ``## <path>`` H2 header followed by exactly
    one fenced code block. File deletions are encoded as
    ``## DELETE: <path>`` with no following code block.

    Strict failure modes:

    - H2 header with no following fenced block → ``reject_format``
    - Duplicate paths (same file declared twice) → ``reject_format``
    - Path with invalid characters (must match ``[A-Za-z0-9_./-]+``)
    - Code block opened but never closed
    """
    lines = text.splitlines()
    files: dict[str, str] = {}
    deleted: list[str] = []

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.startswith("## "):
            i += 1
            continue
        header = line[3:].strip()
        delete = False
        if header.startswith("DELETE:"):
            delete = True
            path = header[len("DELETE:") :].strip()
        else:
            path = header
        if not path:
            raise ParseError(section=f"## {header}", rationale="empty file path in H2 header")
        if not _PATH_RE.fullmatch(path):
            raise ParseError(
                section=f"## {header}",
                rationale=f"path `{path}` contains characters outside [A-Za-z0-9_./-]",
            )
        if delete:
            if path in deleted:
                raise ParseError(
                    section=f"## DELETE: {path}",
                    rationale="duplicate DELETE for the same path",
                )
            deleted.append(path)
            i += 1
            continue

        if path in files:
            raise ParseError(
                section=f"## {path}",
                rationale="duplicate file declaration",
            )

        # Find the next fenced code block. Allow blank lines between
        # header and fence; anything non-blank that is not a fence is a
        # format error so the LLM cannot smuggle freeform prose between
        # the header and the code block.
        j = i + 1
        while j < n and lines[j].strip() == "":
            j += 1
        if j >= n or not _FENCE_OPEN_RE.match(lines[j]):
            raise ParseError(
                section=f"## {path}",
                rationale="missing fenced code block after H2 file header",
            )
        # Scan to matching close fence.
        close = j + 1
        while close < n and not _FENCE_CLOSE_RE.match(lines[close]):
            close += 1
        if close >= n:
            raise ParseError(
                section=f"## {path}",
                rationale="unterminated fenced code block",
            )
        body = "\n".join(lines[j + 1 : close])
        if body and not body.endswith("\n"):
            body = body + "\n"
        files[path] = body
        i = close + 1

    if not files and not deleted:
        raise ParseError(
            section="",
            rationale="stage 3 emission contains no `## <path>` file sections",
        )

    return Stage3Files(files=files, deleted=deleted)


def serialize_stage3(stage3: Stage3Files) -> str:
    out: list[str] = []
    for path in sorted(stage3.files):
        out.append(f"## {path}\n")
        out.append("```\n")
        out.append(stage3.files[path])
        if not stage3.files[path].endswith("\n"):
            out.append("\n")
        out.append("```\n\n")
    for path in stage3.deleted:
        out.append(f"## DELETE: {path}\n\n")
    return "".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


_H1_HEADER_RE = re.compile(r"^# (.+?)\s*$")


def _split_h1_sections(text: str) -> dict[str, str]:
    """Split ``text`` into ``{header_title: body}`` blocks at ``# ``.

    The body is the verbatim text between the header line and the next
    H1 header (or end-of-string). The split is at *exactly* one leading
    ``# `` followed by a non-empty title — H2/H3 headers and code-fence
    octothorpes are NOT confused with H1.
    """
    sections: dict[str, str] = {}
    current_title: str | None = None
    current_body: list[str] = []
    in_fence = False

    for line in text.splitlines():
        # A code fence either opens or closes; while inside a fence the
        # `# ` prefix is just data, never a heading.
        if line.startswith("```"):
            in_fence = not in_fence
            if current_title is not None:
                current_body.append(line)
            continue
        if not in_fence:
            m = _H1_HEADER_RE.match(line)
            if m:
                if current_title is not None:
                    sections[current_title] = "\n".join(current_body).strip()
                current_title = m.group(1).strip()
                current_body = []
                continue
        if current_title is not None:
            current_body.append(line)

    if current_title is not None:
        sections[current_title] = "\n".join(current_body).strip()
    return sections


def _require_single_section(text: str, *, header: str) -> str:
    sections = _split_h1_sections(text)
    if header not in sections:
        raise ParseError(
            section="",
            rationale=f"missing required H1 section `# {header}`",
        )
    extras = [k for k in sections if k != header]
    if extras:
        raise ParseError(
            section="",
            rationale=(
                f"unexpected H1 section(s) in stage emission: {extras}; "
                f"only `# {header}` is allowed"
            ),
        )
    return sections[header]


def _load_yaml(body: str, *, section: str) -> Any:  # noqa: ANN401 — YAML payload is genuinely dynamic
    """Load a YAML body that may or may not be wrapped in a fenced block."""
    stripped = body.strip()
    if stripped.startswith("```"):
        return _load_yaml_block(body, section=section)
    try:
        return yaml.safe_load(stripped)
    except yaml.YAMLError as e:
        raise ParseError(section=section, rationale=f"YAML parse failure: {e}") from None


def _load_yaml_block(body: str, *, section: str) -> Any:  # noqa: ANN401
    stripped = body.strip()
    if not stripped.startswith("```"):
        raise ParseError(
            section=section,
            rationale="expected a fenced ```yaml block",
        )
    lines = stripped.splitlines()
    if not lines or not _FENCE_OPEN_RE.match(lines[0]):
        raise ParseError(section=section, rationale="malformed opening fence")
    # find close
    body_lines = []
    closed = False
    for line in lines[1:]:
        if _FENCE_CLOSE_RE.match(line):
            closed = True
            break
        body_lines.append(line)
    if not closed:
        raise ParseError(section=section, rationale="unterminated YAML fenced block")
    try:
        return yaml.safe_load("\n".join(body_lines))
    except yaml.YAMLError as e:
        raise ParseError(section=section, rationale=f"YAML parse failure: {e}") from None


def _require_str(data: dict[str, Any], key: str, *, section: str) -> str:
    if key not in data:
        raise ParseError(section=section, rationale=f"missing required key `{key}`")
    v = data[key]
    if not isinstance(v, str) or not v.strip():
        raise ParseError(section=section, rationale=f"`{key}` must be a non-empty string")
    return v


def _require_float(data: dict[str, Any], key: str, *, section: str) -> float:
    if key not in data:
        raise ParseError(section=section, rationale=f"missing required key `{key}`")
    v = data[key]
    if isinstance(v, bool):
        raise ParseError(section=section, rationale=f"`{key}` must be a number, not bool")
    if not isinstance(v, (int, float)):
        raise ParseError(section=section, rationale=f"`{key}` must be a number")
    return float(v)


__all__ = [
    "ParseError",
    "Stage1Idea",
    "Stage2Commitments",
    "Stage3Files",
    "parse_stage1",
    "parse_stage2",
    "parse_stage3",
    "serialize_stage1",
    "serialize_stage2",
    "serialize_stage3",
]
