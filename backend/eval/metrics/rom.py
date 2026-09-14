"""
Information retention in condensed ROM versions (goal G2).

Two metrics, both reported separately per version (short, medium):

entity_retention_text
    Fraction of reference must-keep entities (dates, figures, technical terms)
    that appear in the *text* a reader sees. This is the metric that reflects
    the customer complaint: information a reader cannot see is lost.

entity_retention_metadata
    Fraction that appear in the structured metadata fields (technical_terms,
    dates, numbers). Provenance-based merging guarantees this cannot fall
    below the long version's metadata, so it tests the plumbing, not the
    model. Reported so the two are never conflated: high metadata retention
    with low text retention means the facts were kept in the data but written
    out of the prose.

must_keep_recall
    Fraction of reference must-keep points that survive into the short
    version, determined from provenance (source_point_ids and the coverage
    record), not from string matching, so a well-paraphrased point still
    counts.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from eval.metrics.text import contains_entity


def _points(rom_version: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = []
    for agenda in (rom_version or {}).get("agendas", []) or []:
        for point in agenda.get("discussion_points", []) or []:
            if isinstance(point, dict):
                out.append(point)
    return out


def _version_text(rom_version: Dict[str, Any]) -> str:
    return "\n".join(
        str(p.get("polished_text") or p.get("text") or "") for p in _points(rom_version)
    )


def _version_metadata(rom_version: Dict[str, Any]) -> str:
    parts: List[str] = []
    for p in _points(rom_version):
        for field in ("technical_terms", "dates", "numbers"):
            values = p.get(field) or []
            if isinstance(values, str):
                values = [values]
            parts.extend(str(v) for v in values)
    return "\n".join(parts)


def _fraction(found: int, total: int) -> float | None:
    return None if total == 0 else round(found / total, 4)


def entity_retention(rom_version: Dict[str, Any], entities: Iterable[str]) -> Dict[str, Any]:
    entities = [e for e in entities if str(e).strip()]
    text = _version_text(rom_version)
    meta = _version_metadata(rom_version)

    missing_text = [e for e in entities if not contains_entity(text, e)]
    missing_meta = [e for e in entities if not contains_entity(meta, e)]

    return {
        "entities": len(entities),
        "entity_retention_text": _fraction(len(entities) - len(missing_text), len(entities)),
        "entity_retention_metadata": _fraction(len(entities) - len(missing_meta), len(entities)),
        "missing_from_text": missing_text,
        "missing_from_metadata": missing_meta,
    }


def retained_point_ids(rom_version: Dict[str, Any]) -> set[str]:
    """Source points that survived, from provenance rather than text."""
    retained: set[str] = set()
    for agenda in (rom_version or {}).get("agendas", []) or []:
        coverage = agenda.get("condensation_coverage") or {}
        retained.update(str(i) for i in coverage.get("retained_point_ids", []) or [])
        for point in agenda.get("discussion_points", []) or []:
            retained.update(str(i) for i in (point.get("source_point_ids") or []))
    return retained


def must_keep_recall(rom_version: Dict[str, Any], must_keep_ids: Iterable[str]) -> Dict[str, Any]:
    must_keep = [str(i) for i in must_keep_ids]
    kept = retained_point_ids(rom_version)
    dropped = [i for i in must_keep if i not in kept]
    return {
        "must_keep": len(must_keep),
        "must_keep_recall": _fraction(len(must_keep) - len(dropped), len(must_keep)),
        "must_keep_dropped": dropped,
    }
