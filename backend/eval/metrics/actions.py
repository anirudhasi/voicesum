"""
Action-point extraction quality (goal G3).

Matching
    System actions are matched one-to-one to reference actions by content-token
    Jaccard similarity of the task text, greedily in descending similarity,
    accepting a pair only at or above MATCH_THRESHOLD. One-to-one prevents a
    single verbose system action from being credited against several reference
    actions.

action_precision / action_recall / action_f1
    Over the matched pairs, excluding reference actions marked superseded,
    which a correct system should not emit as stated.

owner_accuracy
    Over matched pairs: correct when normalised names agree, or when both are
    unassigned. A correctly-null owner counts as correct. This is deliberate:
    the failure being fixed is confident wrong attribution, and a metric that
    rewarded any non-null answer would reward exactly that failure.

non_null_owner_precision
    Over matched pairs where the system named an owner: the fraction it got
    right. This is the number the reader experiences, since a wrong owner is
    acted upon, and it is the one that must rise.

superseded_emitted
    Reference actions marked superseded that the system nonetheless emitted.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from eval.metrics.text import normalise, token_jaccard

MATCH_THRESHOLD = 0.35

_UNASSIGNED = {"", "none", "null", "n/a", "na", "unassigned", "unknown", "tbd"}


def _owner_key(owner: Optional[str]) -> Optional[str]:
    """Normalised owner, or None when unassigned. Order-insensitive for co-owners."""
    if owner is None:
        return None
    if normalise(owner) in _UNASSIGNED:
        return None
    # Split co-owners on the raw string, before normalisation removes the
    # commas that separate them; otherwise "Alice, Bob" collapses into a
    # single name while "Bob and Alice" splits into two, and they never match.
    parts = re.split(r",|&|/|;|\band\b", str(owner), flags=re.IGNORECASE)
    names = sorted({normalise(p) for p in parts if normalise(p)})
    return ",".join(names) or None


def _task(action: Dict[str, Any]) -> str:
    return str(action.get("task") or action.get("item") or action.get("description") or "")


def match(system: List[Dict[str, Any]], reference: List[Dict[str, Any]]) -> List[Tuple[int, int, float]]:
    """Greedy one-to-one matching. Returns (system_index, reference_index, similarity)."""
    candidates = []
    for si, s in enumerate(system):
        for ri, r in enumerate(reference):
            sim = token_jaccard(_task(s), _task(r))
            if sim >= MATCH_THRESHOLD:
                candidates.append((sim, si, ri))
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))

    used_s, used_r, pairs = set(), set(), []
    for sim, si, ri in candidates:
        if si in used_s or ri in used_r:
            continue
        used_s.add(si)
        used_r.add(ri)
        pairs.append((si, ri, round(sim, 4)))
    return pairs


def _ratio(num: int, den: int) -> Optional[float]:
    return None if den == 0 else round(num / den, 4)


def score(system: List[Dict[str, Any]], reference: List[Dict[str, Any]]) -> Dict[str, Any]:
    live_ref = [r for r in reference if not r.get("superseded")]
    superseded = [r for r in reference if r.get("superseded")]

    pairs = match(system, live_ref)
    matched = len(pairs)

    precision = _ratio(matched, len(system))
    recall = _ratio(matched, len(live_ref))
    f1 = None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = round(2 * precision * recall / (precision + recall), 4)

    owner_correct = 0
    named = 0
    named_correct = 0
    owner_errors = []
    for si, ri, _ in pairs:
        s_owner = _owner_key(system[si].get("owner"))
        r_owner = _owner_key(live_ref[ri].get("owner"))
        correct = s_owner == r_owner
        owner_correct += correct
        if s_owner is not None:
            named += 1
            named_correct += correct
        if not correct:
            owner_errors.append({
                "task": _task(live_ref[ri]),
                "expected": live_ref[ri].get("owner"),
                "got": system[si].get("owner"),
                "owner_source": system[si].get("owner_source"),
            })

    # Only system actions left unmatched can count as emitting a superseded
    # commitment. A correct "book chamber for 5 April" closely resembles the
    # superseded "book chamber for 2 April"; matched against all system actions
    # it would be flagged as both correct and superseded at once.
    matched_system = {si for si, _, _ in pairs}
    leftover = [s for i, s in enumerate(system) if i not in matched_system]
    superseded_pairs = match(leftover, superseded)

    return {
        "system_actions": len(system),
        "reference_actions": len(live_ref),
        "matched": matched,
        "action_precision": precision,
        "action_recall": recall,
        "action_f1": f1,
        "owner_accuracy": _ratio(owner_correct, matched),
        "non_null_owner_precision": _ratio(named_correct, named),
        "owner_errors": owner_errors,
        "superseded_emitted": len(superseded_pairs),
        "missed": [
            _task(r) for ri, r in enumerate(live_ref) if ri not in {p[1] for p in pairs}
        ],
    }
