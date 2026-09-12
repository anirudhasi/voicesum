"""
Point Importance Scoring — W1.2.

Answers the question raised in `Current issue and challenges.docx`:

    "How we can improve and assign importance to the points"

Short and medium ROM versions previously delegated two different decisions to
one opaque model call: *what to drop* and *how to compress*. Compression is a
language task and the model is good at it. Selection is a ranking task over
structured attributes the pipeline has already computed, and handing it to the
model discards that evidence and makes the outcome unrepeatable between runs.

This module scores the ranking half. Every signal below is derived from data
Stage 1 and Stage 2 already produce, so scoring costs no inference and no
model load.

The score is a weighted sum of normalised signals in [0, 1]. It is deliberately
simple and inspectable: when a reviewer disagrees with a drop, the component
scores show why, and the weights are configuration rather than code. A learned
ranker would be harder to explain and impossible to justify to a customer
asking why a particular point disappeared.
"""
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Language that marks a decision or a commitment rather than discussion.
# Word-boundary matched, so "decided" matches but "undecided" does not.
_DECISION_PATTERNS = (
    r"\bagreed?\b", r"\bdecided?\b", r"\bdecision\b", r"\bapproved?\b",
    r"\bconcluded?\b", r"\bresolved?\b", r"\bconfirmed?\b", r"\bfinali[sz]ed?\b",
    r"\bcommit(?:ted|ment)?\b", r"\bwill\s+(?:be\s+)?\w+", r"\bmust\b",
    r"\bshall\b", r"\brequired?\b", r"\bmandat(?:e|ed|ory)\b",
    r"\bsigned?\s+off\b", r"\bauthori[sz]ed?\b", r"\bsanctioned?\b",
)
_DECISION_RE = re.compile("|".join(_DECISION_PATTERNS), re.IGNORECASE)

# Default weights. Tuned to intent, not to data: no labelled set exists yet, so
# these are a starting point to be calibrated against the golden set (W0.1)
# rather than a measured optimum. They are stated here so that is visible.
DEFAULT_WEIGHTS: Dict[str, float] = {
    "has_action_items": 0.30,      # an action is the strongest reason to keep
    "decision_language": 0.20,     # decisions are the point of a meeting record
    "entity_density": 0.15,        # figures, dates and terminology carry detail
    "merge_weight": 0.10,          # a point several others merged into
    "speaker_breadth": 0.10,       # more participants implies broader relevance
    "duration": 0.10,              # time spent is weak but real evidence
    "agenda_confidence": 0.05,     # a confidently mapped point is likelier real
}

_AGENDA_CONFIDENCE_SCORES = {"high": 1.0, "medium": 0.6, "probable": 0.2}

# Saturation points. Beyond these a signal stops adding score, so one very long
# or very crowded point cannot dominate the ranking.
_MAX_SPEAKERS = 4
_MAX_MERGES = 3
_MAX_DURATION_SEC = 300.0
_MAX_ENTITY_DENSITY = 8.0     # entities per 100 words


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _text_of(point: Dict[str, Any]) -> str:
    return str(
        point.get("polished_text")
        or point.get("enhanced_text")
        or point.get("text")
        or point.get("discussion_point")
        or ""
    )


def _count(point: Dict[str, Any], field: str) -> int:
    value = point.get(field) or []
    if isinstance(value, (str, bytes)):
        return 1 if str(value).strip() else 0
    try:
        return len(value)
    except TypeError:
        return 0


def compute_signals(point: Dict[str, Any]) -> Dict[str, float]:
    """
    Return each normalised signal in [0, 1] for one point.

    Returned alongside the score so a reviewer can see *why* a point ranked
    where it did, rather than being given a bare number.
    """
    text = _text_of(point)
    word_count = max(1, len(text.split()))

    entities = (
        _count(point, "technical_terms")
        + _count(point, "dates")
        + _count(point, "numbers")
    )
    density = (entities / word_count) * 100.0

    start = point.get("timeline_start")
    end = point.get("timeline_end")
    duration = 0.0
    if isinstance(start, (int, float)) and isinstance(end, (int, float)):
        duration = max(0.0, float(end) - float(start))

    confidence = str(point.get("agenda_confidence") or point.get("confidence") or "").lower()

    return {
        "has_action_items": 1.0 if _count(point, "action_items") else 0.0,
        "decision_language": 1.0 if _DECISION_RE.search(text) else 0.0,
        "entity_density": _clamp(density / _MAX_ENTITY_DENSITY),
        "merge_weight": _clamp(
            max(0, _count(point, "original_point_ids") - 1) / _MAX_MERGES
        ),
        "speaker_breadth": _clamp(
            max(0, _count(point, "speakers") - 1) / (_MAX_SPEAKERS - 1)
        ),
        "duration": _clamp(duration / _MAX_DURATION_SEC),
        "agenda_confidence": _AGENDA_CONFIDENCE_SCORES.get(confidence, 0.5),
    }


def score_point(
    point: Dict[str, Any],
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Return {"score": float in [0, 1], "signals": {...}, "weights": {...}}.

    The components travel with the score deliberately: they are what make a
    drop explainable and the weights tunable against real reviewer decisions.
    """
    active = dict(DEFAULT_WEIGHTS)
    if weights:
        active.update({k: float(v) for k, v in weights.items() if k in DEFAULT_WEIGHTS})

    signals = compute_signals(point)
    total_weight = sum(active.values()) or 1.0
    score = sum(signals[name] * active[name] for name in signals) / total_weight

    return {"score": round(_clamp(score), 4), "signals": signals, "weights": active}


def annotate(
    points: List[Dict[str, Any]],
    weights: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """
    Attach an `importance` object to each point, in place, and return them.

    Never raises: scoring is advisory, and a failure here must not stop a ROM
    from being produced.
    """
    for point in points or []:
        if not isinstance(point, dict):
            continue
        try:
            point["importance"] = score_point(point, weights)
        except Exception:
            logger.warning(
                "[Importance] Could not score point %s", point.get("id"), exc_info=True
            )
            point["importance"] = {"score": 0.0, "signals": {}, "weights": {}}
    return points


def rank(
    points: List[Dict[str, Any]],
    weights: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """
    Return the points ordered most important first.

    Ties break on the original position, so the ordering is stable and a rerun
    over unchanged input produces an identical result. Determinism is the whole
    reason selection was moved out of the model.
    """
    annotated = annotate(list(points or []), weights)
    indexed = list(enumerate(annotated))
    indexed.sort(key=lambda pair: (-pair[1]["importance"]["score"], pair[0]))
    return [point for _, point in indexed]


def select(
    points: List[Dict[str, Any]],
    target_count: int,
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Choose the `target_count` most important points.

    Returns {"selected": [...], "dropped": [...]}, both in the original
    document order so the record still reads chronologically. Selection is by
    rank; presentation is by position.
    """
    if target_count <= 0:
        return {"selected": [], "dropped": list(points or [])}
    if not points:
        return {"selected": [], "dropped": []}
    if target_count >= len(points):
        return {"selected": annotate(list(points), weights), "dropped": []}

    ranked = rank(points, weights)
    keep_ids = {id(p) for p in ranked[:target_count]}

    selected = [p for p in points if id(p) in keep_ids]
    dropped = [p for p in points if id(p) not in keep_ids]
    return {"selected": selected, "dropped": dropped}
