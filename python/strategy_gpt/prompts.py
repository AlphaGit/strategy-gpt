"""Stage-1 / stage-2 / stage-3 prompt builders for hypothesis-loop emission.

Pure-function prompt construction for the multi-stage candidate emission
path (`hypothesis-loop::multi-stage-candidate-emission`, ADR 0019). Each
builder returns a :class:`StagePrompt` carrying a system message and a
user message; the reasoning client decides how to send them.

The builders intentionally keep ``PROMPT_API.md`` as locked context for
stage 2 and stage 3 (`strategy-runtime::prompt-api-authoritative-llm-
context`). Stage 1 does not need the API surface — idea-level reasoning
is about the diagnosis and the KB, not Rust trait shapes. Embedding the
API there would inflate token usage without changing the model's idea.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .diagnose import Diagnosis
from .hypothesis_loop import HypothesisCandidate, KbCitation, PriorDecision
from .markdown_io import Stage1Idea, Stage2Commitments
from .types import DecisionKind

# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StagePrompt:
    """A prompt ready for a reasoning client.

    ``system`` is the locked instruction text describing the contract,
    invariants, and emission format. ``user`` carries the per-call
    payload (diagnosis, locked prior stages, KB cites, prior decisions).
    Splitting the two so the client can route ``system`` to a system
    role and ``user`` to a user role on both Anthropic and OpenAI.
    """

    system: str
    user: str


# ---------------------------------------------------------------------------
# Stage 1 — idea
# ---------------------------------------------------------------------------

_STAGE1_SYSTEM = """\
You are the idea-generation stage of a quantitative trading strategy research
loop. The user has a baseline strategy and a structured diagnosis of how it
behaved on the most recent backtest. You propose ONE strategy-logic change
that could plausibly improve the backtest's primary objective.

You are NOT proposing parameter tweaks — parameter tuning is handled by a
separate optimizer. You ARE proposing logic changes: adding a component,
replacing a subsystem, removing a heuristic, restructuring an exit rule, etc.

Prefer subtractive changes (removing a parameter, removing a component) when
removal does not break the strategy's underlying thesis. Simpler strategies
that match performance are preferred over more complex ones.

Output a single `# Idea` section in YAML form. The schema is:

    # Idea
    candidate_name: <snake_case_slug, <=40 chars>
    rationale: |
      <free-form prose explaining what to change and why, <=500 chars>
    expected_lift_confidence: <float in [0.0, 1.0]>
    expected_side_effects:
      - <short bullet describing a measurable side-effect>
      - <another bullet>

Do not emit any other H1 section. Do not emit Rust source or YAML for the
falsification claim at this stage — those are later stages. Keep rationale
under 500 characters; longer prose will be truncated.
"""


def _format_kb_cites(cites: list[KbCitation]) -> str:
    if not cites:
        return "(none)"
    lines: list[str] = []
    for i, c in enumerate(cites, start=1):
        excerpt = c.excerpt.strip() if c.excerpt else ""
        excerpt = excerpt[:240]
        lines.append(f"{i}. [{c.source}] {c.locator}")
        if excerpt:
            lines.append(f"   excerpt: {excerpt}")
    return "\n".join(lines)


def _format_prior_decisions(decisions: list[PriorDecision], *, max_n: int = 10) -> str:
    if not decisions:
        return "(none)"
    lines: list[str] = []
    for pd in decisions[:max_n]:
        kind = "accepted" if pd.kind is DecisionKind.ACCEPTED else "rejected"
        rationale = pd.rationale.strip().splitlines()[0][:200] if pd.rationale else ""
        lines.append(f"- [{kind}] {pd.hypothesis.name}: {rationale}")
    if len(decisions) > max_n:
        lines.append(f"  (... {len(decisions) - max_n} more elided)")
    return "\n".join(lines)


def _format_intra_run(history: list[HypothesisCandidate], *, max_n: int = 6) -> str:
    if not history:
        return "(none)"
    lines: list[str] = []
    for c in history[:max_n]:
        lines.append(f"- {c.name}")
    if len(history) > max_n:
        lines.append(f"  (... {len(history) - max_n} more elided)")
    return "\n".join(lines)


def _format_diagnosis(diag: Diagnosis) -> str:
    """Compact, prompt-friendly diagnosis rendering.

    Avoid dumping the full pydantic payload — model token cost is real
    and structured JSON adds quoting noise without helping the model.
    """
    metrics = diag.metrics
    ts = diag.trade_stats
    lines = [
        "Aggregate metrics:",
        f"  sharpe:             {metrics.sharpe:+.4f}",
        f"  sortino:            {metrics.sortino:+.4f}",
        f"  profit_factor:      {metrics.profit_factor:+.4f}",
        f"  win_ratio:          {metrics.win_ratio:.4f}",
        f"  max_drawdown:       {metrics.max_drawdown:+.4f}",
        f"  annualized_return:  {metrics.annualized_return:+.4f}",
        f"  n_trades:           {metrics.n_trades}",
        f"  avg_len_bars:       {metrics.avg_trade_length_bars:.2f}",
        "",
        "Trade stats:",
        f"  n_total={ts.n_total} winners={ts.n_winners} losers={ts.n_losers}",
        f"  avg_pnl={ts.avg_pnl:+.4f} largest_win={ts.largest_winner_pnl:+.4f}"
        f" largest_loss={ts.largest_loser_pnl:+.4f}",
        f"  long={ts.long_count} short={ts.short_count}",
    ]
    if diag.regime_performance:
        lines.append("")
        lines.append("Regime performance:")
        for rp in diag.regime_performance:
            lines.append(
                f"  {rp.label}: n={rp.n_trades} total_pnl={rp.total_pnl:+.4f}"
                f" win_rate={rp.win_rate:.4f} coverage_bars={rp.coverage_bars}"
            )
    if diag.signal_misfires:
        lines.append("")
        lines.append("Signal misfires:")
        for sm in diag.signal_misfires:
            lines.append(
                f"  {sm.signal}: fired={sm.fired_count} used={sm.used_count}"
                f" suppressed={sm.suppressed_count} fired_no_trade={sm.fired_no_trade_count}"
            )
    if diag.exec_log_summary:
        lines.append("")
        lines.append("Exec log summary:")
        for k, v in sorted(diag.exec_log_summary.items()):
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def build_stage1_prompt(
    *,
    strategy_name: str,
    diagnosis: Diagnosis,
    kb_cites: list[KbCitation],
    prior_decisions: list[PriorDecision],
    intra_run_history: list[HypothesisCandidate] | None = None,
) -> StagePrompt:
    """Build the stage-1 (idea) prompt.

    Inputs:

    - ``strategy_name`` — the parent strategy crate name; the candidate
      proposes a logic change *to* this strategy.
    - ``diagnosis`` — the structured backtest diagnosis. Embedded in
      compact form to keep token usage manageable.
    - ``kb_cites`` — knowledge-base citations already filtered by
      :func:`strategy_gpt.kb_query.kb_filter_node`. The prompt encourages
      the model to ground its idea in these citations.
    - ``prior_decisions`` — prior-run accepted/rejected decisions. The
      prompt nudges the model away from rejected directions and toward
      accepted ones.
    - ``intra_run_history`` — candidates emitted earlier in the *current*
      hypothesize run (helps the inner loop avoid emitting duplicates of
      its own recent ideas).
    """
    intra = intra_run_history or []
    user = (
        f"## Strategy under hypothesis\n\n"
        f"`{strategy_name}`\n\n"
        f"## Diagnosis of the most recent backtest\n\n"
        f"{_format_diagnosis(diagnosis)}\n\n"
        f"## Knowledge-base citations (post-filter)\n\n"
        f"{_format_kb_cites(kb_cites)}\n\n"
        f"## Prior decisions on this strategy\n\n"
        f"{_format_prior_decisions(prior_decisions)}\n\n"
        f"## Candidates emitted earlier in THIS run\n\n"
        f"{_format_intra_run(intra)}\n\n"
        f"---\n\n"
        f"Emit a single `# Idea` YAML section per the system contract."
    )
    return StagePrompt(system=_STAGE1_SYSTEM, user=user)


# ---------------------------------------------------------------------------
# Stage 2 — commitments
# ---------------------------------------------------------------------------

_STAGE2_SYSTEM = """\
You are the commitments stage of a quantitative trading strategy research
loop. The user has accepted your stage-1 idea. You now commit to:

1. A measurable comparative falsification claim, including a primary metric
   delta versus the baseline plus zero or more guard constraints.
2. The parameter intent of your change: which parameters are added (with
   bounds), which are kept from the baseline schema, and which are removed.

Output TWO H1 sections — `# Falsification` and `# ParamIntent` — each
containing one fenced YAML block. Do not emit any other section. Do not
re-open the idea or rationale: those are locked.

Falsification schema:

    # Falsification
    ```yaml
    primary:
      metric: <one of the allowed BacktestMetrics names>
      direction: gt | gte | lt | lte
      delta_vs_baseline: <float>
      scope:
        kind: aggregate | regime | fold | window
        # regime/fold/window kinds add their key (regime, fold, window_start,
        # window_end) — see the markdown_io contract docstring.
    guard_constraints:
      - { metric: <name>, direction: lte, delta_vs_baseline: <float> }
      - { metric: <name>, direction: gte, factor: <float> }
    ```

Guard constraints are REQUIRED for any metric you expect to move
significantly. The downstream tester evaluates BOTH the primary claim and
the guards; failing a guard rejects the candidate regardless of primary.

ParamIntent schema:

    # ParamIntent
    ```yaml
    added:
      - { name: <name>, kind: f64 | i64 | bool | string,
          min: <float?>, max: <float?>, default: <value> }
    kept: [ <name>, <name> ]
    removed: [ <name> ]
    ```

Added params MUST declare `min` and `max` for numeric kinds. `kept` and
`removed` reference the names declared in the baseline `params_schema.json`
shown below.

You MAY only use parameter names compatible with the engine-rt
`ParamSchema` convention; consult the embedded PROMPT_API document for the
declaration grammar.
"""


def _format_params_schema(schema: dict[str, Any] | None) -> str:
    if schema is None:
        return "(baseline has no declared params)"
    dumped: str = yaml.safe_dump(schema, sort_keys=False)
    return dumped.strip()


def _format_allowed_metrics(metrics: list[str]) -> str:
    if not metrics:
        return "(unrestricted)"
    return ", ".join(sorted(metrics))


def build_stage2_prompt(  # noqa: PLR0913 — prompt assembly takes the full context list
    *,
    strategy_name: str,
    stage1_response: str,
    stage1_parsed: Stage1Idea,
    prompt_api: str,
    baseline_params_schema: dict[str, Any] | None,
    allowed_metrics: list[str],
) -> StagePrompt:
    """Build the stage-2 (commitments) prompt.

    Inputs:

    - ``stage1_response`` — the *verbatim* stage-1 emission, embedded
      as locked context (`hypothesis-loop::stage-3-sees-stage-1-and-
      stage-2-as-locked-context`, applied here at stage 2).
    - ``stage1_parsed`` — the parsed stage-1 idea; surfaced in compact
      form so the model has the salient name + rationale handy.
    - ``prompt_api`` — the verbatim contents of
      ``crates/engine-rt/PROMPT_API.md``; embedded so the model can
      verify its `param_intent` schema choices against the runtime.
    - ``baseline_params_schema`` — the baseline strategy's declared
      ``params_schema.json`` body (already validated by build-pipeline),
      surfaced so ``kept``/``removed`` names cross-check.
    - ``allowed_metrics`` — the names valid for the falsification's
      primary and guard metrics. The downstream parser hard-rejects on
      mismatch; surfacing the list up-front saves a repair round trip.
    """
    user = (
        f"## Strategy under hypothesis\n\n"
        f"`{strategy_name}`\n\n"
        f"## Locked stage-1 idea (verbatim)\n\n"
        f"```\n{stage1_response.rstrip()}\n```\n\n"
        f"## Idea summary\n\n"
        f"- candidate_name: `{stage1_parsed.candidate_name}`\n"
        f"- expected_lift_confidence: {stage1_parsed.expected_lift_confidence}\n"
        f"- expected_side_effects: {stage1_parsed.expected_side_effects}\n\n"
        f"## Allowed BacktestMetrics names\n\n"
        f"{_format_allowed_metrics(allowed_metrics)}\n\n"
        f"## Baseline params_schema.json\n\n"
        f"```yaml\n{_format_params_schema(baseline_params_schema)}\n```\n\n"
        f"## engine-rt PROMPT_API (locked reference)\n\n"
        f"```markdown\n{prompt_api.rstrip()}\n```\n\n"
        f"---\n\n"
        f"Emit `# Falsification` and `# ParamIntent` YAML sections per the system contract."
    )
    return StagePrompt(system=_STAGE2_SYSTEM, user=user)


# ---------------------------------------------------------------------------
# Stage 3 — files
# ---------------------------------------------------------------------------

_STAGE3_SYSTEM = """\
You are the file-emission stage of a quantitative trading strategy research
loop. Stages 1 (idea) and 2 (commitments) are LOCKED — you may not re-open
them. Your job is to produce the Rust source for the candidate strategy
crate, conforming to the locked commitments and to the engine-rt
PROMPT_API document.

Output format — each file is a `## <path>` H2 header followed by a single
fenced code block. Deletions are encoded as `## DELETE: <path>` with no
following code block. Example:

    ## Cargo.toml
    ```toml
    [package]
    ...
    ```

    ## src/lib.rs
    ```rust
    use engine_rt::{Strategy, Context, ...};
    ...
    ```

    ## DELETE: src/old_module.rs

Hard constraints:

- Only declare dependencies from the allowed-crate whitelist (see
  PROMPT_API §6). Adding any other crate hard-rejects the candidate.
- Implement the sealed `Strategy` trait exactly as declared in the
  `engine-rt source` section of the user message. That section is the
  authoritative reference: every method name, arity, parameter type,
  and return type the strategy calls MUST appear there. Do NOT invent
  methods or types.
- Emit a `params_schema.json` whose schema matches the locked stage-2
  `ParamIntent`.
- Do NOT emit `unsafe`, `extern`, threads, network code, or filesystem
  code (PROMPT_API §1). The linter rejects them.

Begin every emission with `Cargo.toml`. End every emission with the
sealed `strategy_entry!(factory)` invocation at the bottom of
`src/lib.rs`.
"""


_BASELINE_FILE_MAX_BYTES = 8 * 1024


def _format_baseline_files(files: dict[str, str]) -> str:
    if not files:
        return "(baseline has no source files; emit fresh sources)"
    lines: list[str] = []
    for path in sorted(files):
        body = files[path]
        # Cap each baseline file at 8 KiB to keep token usage bounded;
        # the model gets the file's shape without paying for unrelated
        # long indicators code.
        truncated = (
            body
            if len(body) <= _BASELINE_FILE_MAX_BYTES
            else body[:_BASELINE_FILE_MAX_BYTES] + "\n... <truncated>\n"
        )
        fence = "rust" if path.endswith(".rs") else ("toml" if path.endswith(".toml") else "")
        lines.append(f"### {path}\n")
        lines.append(f"```{fence}\n{truncated.rstrip()}\n```\n")
    return "\n".join(lines)


def _load_engine_rt_surface(src_dir: Path) -> str:
    """Concatenate every ``.rs`` file under ``src_dir`` into a single block.

    Returned format is one ``### <filename>`` H3 header per file followed
    by a fenced Rust block carrying the verbatim contents. The caller
    embeds the block into the stage-3 prompt so the LLM sees the
    authoritative trait + supporting-type surface. Reading from disk per
    call means trait edits propagate automatically; the prompt has no
    second source of truth to drift from.
    """
    if not src_dir.is_dir():
        return f"(engine-rt source dir not found at {src_dir})"
    parts: list[str] = []
    for path in sorted(src_dir.glob("*.rs")):
        try:
            body = path.read_text(encoding="utf-8")
        except OSError as e:
            parts.append(f"### {path.name}\n\n(unreadable: {e})\n")
            continue
        parts.append(f"### {path.name}\n\n```rust\n{body.rstrip()}\n```\n")
    if not parts:
        return f"(no .rs files under {src_dir})"
    return "\n".join(parts)


def build_stage3_prompt(  # noqa: PLR0913
    *,
    strategy_name: str,
    stage1_response: str,
    stage2_response: str,
    stage2_parsed: Stage2Commitments,
    prompt_api: str,
    baseline_files: dict[str, str],
    engine_rt_src_dir: Path | None = None,
) -> StagePrompt:
    """Build the stage-3 (files) prompt.

    Inputs:

    - ``stage1_response`` / ``stage2_response`` — the *verbatim*
      previous-stage emissions, both locked into context.
    - ``stage2_parsed`` — surfaced so the model has a clean view of the
      param intent and falsification claim it must implement.
    - ``prompt_api`` — verbatim PROMPT_API.md.
    - ``baseline_files`` — the baseline strategy crate's source map
      (path → content). Files larger than 8 KiB are truncated.
    - ``engine_rt_src_dir`` — when set, every ``.rs`` file under that
      directory is concatenated into the prompt as the authoritative
      trait surface. Reading from disk means trait changes (e.g. a new
      ``Context`` method) flow into the next LLM call automatically.
    """
    surface_block = ""
    if engine_rt_src_dir is not None:
        surface_block = (
            f"## engine-rt source (authoritative trait surface)\n\n"
            f"This is the verbatim contents of every `.rs` file under "
            f"`engine-rt/src/`. The `Context` trait, `Strategy` trait, "
            f"and supporting types (`Side`, `OrderId`, `Position`, `Bar`, "
            f"`Result`, …) are defined here. Method names, arities, and "
            f"return types listed here are authoritative — do NOT invent "
            f"any other method, type, or signature.\n\n"
            f"{_load_engine_rt_surface(engine_rt_src_dir)}\n\n"
        )
    user = (
        f"## Strategy under hypothesis\n\n"
        f"`{strategy_name}`\n\n"
        f"## Locked stage-1 idea (verbatim)\n\n"
        f"```\n{stage1_response.rstrip()}\n```\n\n"
        f"## Locked stage-2 commitments (verbatim)\n\n"
        f"```\n{stage2_response.rstrip()}\n```\n\n"
        f"## Stage-2 falsification (parsed JSON)\n\n"
        f"```json\n{json.dumps(stage2_parsed.falsification, indent=2)}\n```\n\n"
        f"## Stage-2 param intent (parsed JSON)\n\n"
        f"```json\n{json.dumps(stage2_parsed.param_intent, indent=2)}\n```\n\n"
        f"## Baseline strategy crate (files)\n\n"
        f"{_format_baseline_files(baseline_files)}\n\n"
        f"{surface_block}"
        f"## engine-rt PROMPT_API (locked reference)\n\n"
        f"```markdown\n{prompt_api.rstrip()}\n```\n\n"
        f"---\n\n"
        f"Emit one `## <path>` section per file per the system contract. Begin with `Cargo.toml`."
    )
    return StagePrompt(system=_STAGE3_SYSTEM, user=user)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_prompt_api(repo_root: Path) -> str:
    """Read ``crates/engine-rt/PROMPT_API.md`` from the repository.

    Centralised so the orchestrator can pass a single, validated copy to
    every stage builder rather than re-reading the file per call. Raises
    :class:`FileNotFoundError` if the document is missing — the loop
    cannot operate without it.
    """
    path = repo_root / "crates" / "engine-rt" / "PROMPT_API.md"
    return path.read_text(encoding="utf-8")


__all__ = [
    "StagePrompt",
    "build_stage1_prompt",
    "build_stage2_prompt",
    "build_stage3_prompt",
    "load_prompt_api",
]
