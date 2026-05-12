"""Hypothesis-loop ``kb_query`` node.

Spec `hypothesis-loop::knowledge-base-queries-with-citation-capture`: after
``diagnose``, the loop queries the knowledge base with a domain-relevant
query string and attaches the returned citations to ``state.kb_cites``.
Every citation carries source provenance sufficient for downstream
consumers (the Hypothesis Loop's ``generate`` node, the rationale
generator) to surface them to the user.

The node is a pure function over :class:`HypothesisLoopState`. The KB
itself is consumed through the :class:`KbClient` protocol so tests can
stub retrieval without standing up the native extension.
"""

from __future__ import annotations

from typing import Protocol

from .hypothesis_loop import HypothesisLoopState, KbCitation


class _KbProvenance(Protocol):
    source_id: str
    title: str
    author: str | None
    year: int | None
    section: str | None
    page: int | None


class _KbItem(Protocol):
    chunk_id: str
    text: str
    score: float
    provenance: _KbProvenance


class _KbResult(Protocol):
    items: list[_KbItem]


class KbClient(Protocol):
    """Minimal retrieval surface the node depends on.

    Matches :meth:`strategy_gpt.kb.KnowledgeBase.retrieve` structurally so
    the native-backed wrapper drops in without an adapter; tests pass a
    stub that returns canned :class:`KbCitation`-shaped items.
    """

    def retrieve(self, query: str, k: int) -> _KbResult: ...


def _provenance_to_citation(item: _KbItem) -> KbCitation:
    """Project a KB retrieval item to a :class:`KbCitation` record.

    ``locator`` composes the most specific reference available — section,
    page, then chunk id — so downstream prompts can render a stable,
    human-meaningful citation tag. ``excerpt`` carries the chunk text so
    the generator can ground claims directly without a second KB call."""
    parts: list[str] = []
    if item.provenance.section:
        parts.append(item.provenance.section)
    if item.provenance.page is not None:
        parts.append(f"p.{item.provenance.page}")
    if not parts:
        parts.append(item.chunk_id)
    return KbCitation(
        source=item.provenance.source_id,
        locator=", ".join(parts),
        excerpt=item.text,
    )


def kb_query_node(
    state: HypothesisLoopState,
    *,
    client: KbClient,
    query: str | None = None,
    k: int = 6,
) -> HypothesisLoopState:
    """Retrieve top-``k`` KB items and attach them as ``kb_cites``.

    Query construction: when ``query`` is supplied, use it verbatim; this
    is the path used by the orchestrator-side workflow which composes a
    diagnosis-derived prompt. When ``query`` is ``None``, derive a query
    from the diagnosis (regime labels + signal names) so the node can run
    standalone in tests. Either way, the returned citations are appended
    to the existing ``state.kb_cites`` so the loop accumulates citations
    across iterations.
    """
    resolved_query = query if query is not None else _derive_query(state)
    if not resolved_query:
        # No diagnosis-derived query and no caller-supplied query — leave
        # state untouched. The generate node tolerates an empty kb_cites
        # list; this just means the run gets no KB grounding this turn.
        return state
    result = client.retrieve(resolved_query, k)
    new_cites = [_provenance_to_citation(item) for item in result.items]
    return state.model_copy(update={"kb_cites": [*state.kb_cites, *new_cites]})


def _derive_query(state: HypothesisLoopState) -> str:
    """Compose a retrieval query from the current diagnosis.

    Uses regime labels (e.g. ``high_vol``, ``downtrend``) and signal names
    (e.g. ``rsi_oversold``) — both terms a curated KB will index against.
    Empty diagnosis or empty fields return ``""`` so the caller can decide
    to skip the retrieval call. Deterministic ordering keeps replay byte-
    identical.
    """
    diagnosis = state.diagnosis
    if diagnosis is None:
        return ""
    parts: list[str] = []
    regimes = getattr(diagnosis, "regime_performance", None)
    if regimes is not None:
        # `regime_performance` is a list of RegimePerformance records (label
        # + stats). Take labels in order; sort for determinism.
        labels = sorted({getattr(r, "label", "") for r in regimes if getattr(r, "label", "")})
        parts.extend(labels)
    misfires = getattr(diagnosis, "signal_misfires", None)
    if misfires is not None:
        names = sorted({getattr(m, "signal", "") for m in misfires if getattr(m, "signal", "")})
        parts.extend(names)
    return " ".join(parts).strip()


__all__ = ["KbClient", "kb_query_node"]
