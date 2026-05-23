"""Stage prompts for the author command.

Two prompt scaffolds:

- :func:`build_dialog_system_prompt` — opens an interactive dialog whose
  job is to elicit a structured ``AuthorIntent`` from the operator.
- :func:`build_emit_prompt` — hands a frozen intent to the LLM and asks
  it to emit ``Cargo.toml`` + ``src/lib.rs`` + ``smoke.toml`` (and
  optionally ``experiment.yaml``) as a stage-3-shaped markdown payload.

Both prompts embed the build-pipeline whitelist and the always-on
few-shot exemplars (``vxx-strategy`` + ``example-strategy``). The
exemplars are looked up at prompt-build time so the prompts stay in
sync with the crates on disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .prompts import StagePrompt

if TYPE_CHECKING:
    from .author import AuthorIntent

# ---------------------------------------------------------------------------
# Exemplar loading
# ---------------------------------------------------------------------------


_EXEMPLAR_CRATES: tuple[str, ...] = ("vxx-strategy", "example-strategy")
_EXEMPLAR_FILES: tuple[str, ...] = ("Cargo.toml", "src/lib.rs")


@dataclass(frozen=True, slots=True)
class _Exemplar:
    crate: str
    path: str
    body: str


def _load_exemplars(crates_dir: Path) -> list[_Exemplar]:
    """Load the static few-shot exemplars from the workspace.

    Missing files are silently skipped — the spec requires the
    exemplars to be present, but during early development a partial
    workspace shouldn't crash the prompt builder. The validate step
    catches a regression here.
    """
    out: list[_Exemplar] = []
    for crate in _EXEMPLAR_CRATES:
        for rel in _EXEMPLAR_FILES:
            path = crates_dir / crate / rel
            if not path.exists():
                continue
            out.append(_Exemplar(crate=crate, path=rel, body=path.read_text(encoding="utf-8")))
    return out


def _format_exemplars(exemplars: list[_Exemplar]) -> str:
    if not exemplars:
        return "(no exemplars available; the workspace is missing the reference crates)"
    lines: list[str] = []
    for ex in exemplars:
        fence = "rust" if ex.path.endswith(".rs") else ("toml" if ex.path.endswith(".toml") else "")
        lines.append(f"### {ex.crate}/{ex.path}\n")
        lines.append(f"```{fence}\n{ex.body.rstrip()}\n```\n")
    return "\n".join(lines)


def _load_whitelist(crates_dir: Path) -> str:
    """Read the build-pipeline whitelist verbatim for embedding in prompts."""
    path = crates_dir / "build-pipeline" / "whitelist.toml"
    if not path.exists():
        return "(whitelist not found; treat as unknown — emit nothing outside the standard set)"
    return path.read_text(encoding="utf-8")


def _load_prompt_api(crates_dir: Path) -> str:
    """Read the engine-rt PROMPT_API.md verbatim."""
    path = crates_dir / "engine-rt" / "PROMPT_API.md"
    if not path.exists():
        return "(PROMPT_API.md not found)"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Dialog stage
# ---------------------------------------------------------------------------


_DIALOG_SYSTEM_TEMPLATE = """\
You are the author-dialog stage of a quantitative trading strategy research
loop. Your job is to elicit a structured `AuthorIntent` from the operator
through clarifying questions, then commit to it by emitting a single
`# AuthorIntent` section. You DO NOT emit Rust code during the dialog —
that is a separate downstream stage.

Behavior:

1. If the operator's seed is missing or vague, ask ONE focused clarifying
   question per turn. Cover (in roughly this order) universe / instrument
   selection, mechanism summary, the parameter sketch the strategy will
   expose, and the smoke fixture window. Keep each question short.
2. As soon as you have enough information to author a coherent strategy
   crate, emit the final intent. Don't keep asking once the operator has
   answered the basics.
3. Propose a snake-case `name` (no `-strategy` suffix; the build pipeline
   appends it). The crate directory will be `crates/<name>-strategy/`.
4. The smoke fixture should be small but non-trivial — a few months of
   daily bars on a liquid instrument is typical.

## DecisionsSoFar block (REQUIRED on every turn)

At the TOP of every reply (clarifying question OR final intent), emit a
structured `# DecisionsSoFar` block summarizing every decision that is
currently locked in. This block is the authoritative source of state —
chat history may be compacted, but this block is replayed back to you on
every subsequent turn so you can resume reliably.

Format:

    # DecisionsSoFar
    ```yaml
    crate_name: <name>            # once proposed and accepted
    universe: <symbols/scope>     # once committed
    mechanism_summary: |          # once described
      ...
    param_sketch:                 # once sketched
      params: [...]
    smoke_spec:                   # once chosen
      symbol: ...
      resolution: ...
      start: ...
      end: ...
      provider: ...
    experiment_spec: ...          # only if --verify=batch was requested
    edit_mode_target: <path>      # only if editing an existing crate
    ```

Rules for the block:

- Use EXACTLY these field names: `crate_name`, `universe`, `mechanism_summary`,
  `param_sketch`, `smoke_spec`, `experiment_spec`, `edit_mode_target`. Do
  not invent new fields.
- Only include a field once you and the operator have agreed on its value.
  Omit fields that are still pending.
- If the operator revises a previously-locked decision, update the block
  to reflect the new value. The dialog driver detects amendments by
  diffing against the prior block.
- The block goes ABOVE any prose. After the block, write your clarifying
  question (or the final `# AuthorIntent` block).

Final intent format (single section, fenced YAML):

    # AuthorIntent
    ```yaml
    name: <snake_case_or_kebab, <=40 chars, lowercase>
    description: |
      <free-form prose describing what this strategy does>
    mechanism_summary: |
      <2-5 sentences describing the entry/exit mechanism>
    param_schema_sketch:
      params:
        - { name: <name>, kind: f64 | i64 | bool | string,
            min: <float?>, max: <float?>, default: <value> }
    smoke_spec:
      symbol: <symbol>
      resolution: 1d | 1h | 1m
      start: <YYYY-MM-DD>
      end: <YYYY-MM-DD>
      provider: yfinance
    experiment_spec: <optional; omit when --verify=batch was not requested>
    ```

Constraints carried into the downstream emit stage:

- The crate may only depend on crates in the whitelist below. The
  downstream stage rejects any non-whitelisted dependency.
- The crate implements the sealed `Strategy` trait declared in the
  engine-rt PROMPT_API. Few-shot exemplars are loaded below.

## Allowed-crate whitelist

```toml
__WHITELIST__
```

## Few-shot exemplars

__EXEMPLARS__

## engine-rt PROMPT_API (locked reference)

```markdown
__PROMPT_API__
```
"""


def build_dialog_system_prompt(*, crates_dir: Path) -> str:
    """Return the dialog system prompt text."""
    return (
        _DIALOG_SYSTEM_TEMPLATE.replace("__WHITELIST__", _load_whitelist(crates_dir).rstrip())
        .replace("__EXEMPLARS__", _format_exemplars(_load_exemplars(crates_dir)))
        .replace("__PROMPT_API__", _load_prompt_api(crates_dir).rstrip())
    )


def format_decisions_for_prompt(projection: dict[str, object]) -> str:
    """Render the current decisions projection as a user-prompt section.

    Used to inject locked-in state into the next dialog turn so that a
    compacted chat history does not lose the decisions. The LLM is
    instructed elsewhere that this section is authoritative.
    """
    import yaml  # noqa: PLC0415 — local to avoid import-time YAML cost

    if not projection:
        return ""
    body = yaml.safe_dump(projection, sort_keys=False, allow_unicode=True).rstrip()
    return (
        "## DecisionsSoFar (authoritative; resume from this)\n\n"
        f"```yaml\n{body}\n```\n"
    )


# ---------------------------------------------------------------------------
# Emit stage
# ---------------------------------------------------------------------------


_EMIT_SYSTEM_TEMPLATE = """\
You are the file-emission stage of the strategy-gpt author command. The
operator has accepted a structured `AuthorIntent` (shown below). Your job
is to emit a working strategy crate that compiles, passes lint, and
passes a smoke backtest on the declared fixture.

Output format — each file is a `## <path>` H2 header followed by a single
fenced code block. You MUST emit, at minimum:

    ## Cargo.toml
    ```toml
    [package]
    ...
    ```

    ## src/lib.rs
    ```rust
    use engine_rt::{Strategy, Context, ...};
    ...
    strategy_entry!(factory);
    ```

    ## smoke.toml
    ```toml
    symbol = "<symbol>"
    resolution = "1d"
    start = "<YYYY-MM-DD>"
    end = "<YYYY-MM-DD>"
    provider = "yfinance"
    ```

You MAY emit additional `## src/<module>.rs` files when the strategy is
non-trivial. Begin with `Cargo.toml`.

Hard constraints:

- Only declare dependencies from the allowed-crate whitelist (below).
  Adding any other crate hard-rejects the emission.
- `engine-rt` MUST be declared as a path dependency:
  `engine-rt = { path = "../engine-rt" }`. Never use a version string
  (`engine-rt = "*"` or `engine-rt = "0.1"`); the crate is not
  published. The build pipeline normalizes this on write, but the
  LLM should emit it correctly the first time.
- Implement the sealed `Strategy` trait exactly as declared in the
  engine-rt PROMPT_API.
- Do NOT emit `unsafe`, `extern`, threads, network code, or filesystem
  code. The linter rejects them.
- Do NOT emit narration outside the file sections.

## Allowed-crate whitelist

```toml
__WHITELIST__
```

## Few-shot exemplars

__EXEMPLARS__

## engine-rt PROMPT_API (locked reference)

```markdown
__PROMPT_API__
```
"""


def build_emit_prompt(
    *,
    intent: AuthorIntent,
    feedback: str,
    crates_dir: Path,
    previous_emission: str = "",
) -> StagePrompt:
    """Build the emit-stage prompt for a frozen intent.

    ``feedback`` is the empty string on the initial attempt and the
    synthesized repair feedback on subsequent attempts. ``previous_emission``
    is the raw text of the last failed attempt; including it gives the
    LLM a stable baseline to diff against the failure feedback rather
    than re-deriving the whole crate from scratch. Both are empty on
    the first attempt.
    """
    system = (
        _EMIT_SYSTEM_TEMPLATE.replace("__WHITELIST__", _load_whitelist(crates_dir).rstrip())
        .replace("__EXEMPLARS__", _format_exemplars(_load_exemplars(crates_dir)))
        .replace("__PROMPT_API__", _load_prompt_api(crates_dir).rstrip())
    )
    user_sections = [
        "## AuthorIntent (frozen)\n",
        f"```yaml\n{_intent_yaml(intent).rstrip()}\n```\n",
    ]
    if intent.baseline_crate is not None:
        user_sections.append(_render_baseline_section(intent.baseline_crate))
    if previous_emission:
        user_sections.append(
            "## Your previous attempt (revise this; do not start from scratch)\n\n"
            f"{previous_emission.rstrip()}\n"
        )
    if feedback:
        user_sections.append(
            "## Why the previous attempt was rejected\n\n"
            f"{feedback.rstrip()}\n\n"
            "Focus your changes on what the feedback above identifies. Preserve "
            "everything else from your previous attempt verbatim unless the "
            "feedback requires changing it.\n"
        )
    user_sections.append("---\n\nEmit the file payload now.")
    return StagePrompt(system=system, user="\n".join(user_sections))


def _intent_yaml(intent: AuthorIntent) -> str:
    """Render the frozen intent as YAML for the emit prompt.

    Goes through ``yaml.safe_dump`` rather than the persisted TOML
    format so the LLM gets the clean, declarative shape it produced
    during the dialog rather than the on-disk record format.
    """
    import yaml  # noqa: PLC0415 — already imported elsewhere; keep here for clarity

    smoke = intent.smoke_spec
    body: dict[str, object] = {
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


def _render_baseline_section(baseline: Path) -> str:
    """Embed the existing crate's source files when in edit-mode."""
    lines = ["## Baseline crate (edit-mode; emit modifications)\n"]
    for rel in ("Cargo.toml", "src/lib.rs", "smoke.toml", "intent.toml"):
        path = baseline / rel
        if not path.exists():
            continue
        fence = "rust" if rel.endswith(".rs") else "toml"
        lines.append(f"### {rel}\n")
        lines.append(f"```{fence}\n{path.read_text(encoding='utf-8').rstrip()}\n```\n")
    return "\n".join(lines)


_AMEND_INTENT_TEMPLATE = """\
You are revising a previously-accepted `AuthorIntent` because the
emit/build/smoke loop hit its repair budget and control was returned to
the operator. The operator has provided guidance below; produce a new
`# AuthorIntent` block that incorporates it.

Constraints:

- Keep the same `name` unless the operator's guidance explicitly asks for
  a rename. The on-disk crate directory will not change.
- Preserve unrelated fields verbatim. Only revise what the guidance
  implies needs to change.
- Output a single `# AuthorIntent` block. No prose around it. No
  DecisionsSoFar block, no preamble. Just the fenced YAML payload.

## Previous intent

```yaml
__PREV_INTENT__
```

## Repair failure trail

__FAILURE_TRAIL__

## Operator guidance

__GUIDANCE__

__SCOPE_HINT__
"""


def build_amend_intent_prompt(
    *,
    previous_intent_yaml: str,
    failure_trail: str,
    guidance: str,
    scope_field: str | None,
) -> str:
    """Build the system prompt for an intent-amendment LLM turn.

    ``scope_field`` constrains the revision to a single field of the
    intent (e.g. ``param_sketch``). When ``None``, the LLM may rewrite
    any field that the guidance affects.
    """
    scope_hint = (
        f"## Scope\n\nRevise ONLY the `{scope_field}` field; leave all other fields verbatim.\n"
        if scope_field is not None
        else ""
    )
    return (
        _AMEND_INTENT_TEMPLATE.replace("__PREV_INTENT__", previous_intent_yaml.rstrip())
        .replace("__FAILURE_TRAIL__", failure_trail.rstrip() or "(no trail recorded)")
        .replace("__GUIDANCE__", guidance.rstrip())
        .replace("__SCOPE_HINT__", scope_hint.rstrip())
    )


__all__ = [
    "build_amend_intent_prompt",
    "build_dialog_system_prompt",
    "build_emit_prompt",
    "format_decisions_for_prompt",
]
