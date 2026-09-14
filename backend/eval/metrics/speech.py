"""
Transcription, diarization and speaker identification (goal G1).

word_error_rate
    jiwer WER after eval.metrics.text.normalise on both sides. Normalisation is
    part of the definition; see that module.

diarization_error_rate, jaccard_error_rate
    pyannote.metrics, with a 0.25 s forgiveness collar around reference
    boundaries (the NIST convention) and overlapping speech scored rather than
    skipped. These measure *who spoke when* under an optimal label mapping, so
    they are independent of whether the names are right.

speaker_confusion_rate
    Duration-weighted fraction of reference speech, by enrolled speakers, where
    the system's resolved name differs from the reference name. No mapping is
    applied: names are compared directly, because identification is exactly
    the question of whether the name is right. This is the metric for the
    reported "wrong speaker assignment" edge cases.

short_turn_confusion_rate
    The same, restricted to reference turns shorter than SHORT_TURN_SEC, where
    too little speech exists for a reliable voice embedding and assignment
    falls back on proximity.

unmatched_rate
    Duration-weighted fraction of enrolled speakers' speech left under a
    generic label ("Speaker 1") rather than a name.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from eval.metrics.text import normalise

COLLAR_SEC = 0.25
SHORT_TURN_SEC = 2.5
_GENERIC = re.compile(r"^(speaker[\s_]*\d+|speaker_\d+|unknown|\?)$", re.IGNORECASE)


def word_error_rate(reference: str, hypothesis: str) -> Optional[float]:
    import jiwer

    ref, hyp = normalise(reference), normalise(hypothesis)
    if not ref:
        return None
    return round(float(jiwer.wer(ref, hyp)), 4)


def _annotation(turns: List[Dict[str, Any]], label_key: str):
    from pyannote.core import Annotation, Segment

    ann = Annotation()
    for i, t in enumerate(turns):
        start, end = float(t["start"]), float(t["end"])
        if end > start:
            ann[Segment(start, end), i] = str(t.get(label_key) or "UNKNOWN")
    return ann


def diarization_errors(reference_turns, system_segments) -> Dict[str, Optional[float]]:
    try:
        from pyannote.metrics.diarization import DiarizationErrorRate, JaccardErrorRate
    except ImportError:
        return {"diarization_error_rate": None, "jaccard_error_rate": None,
                "note": "pyannote.metrics not installed"}

    ref = _annotation(reference_turns, "speaker")
    hyp = _annotation(system_segments, "speaker")
    der = DiarizationErrorRate(collar=COLLAR_SEC, skip_overlap=False)(ref, hyp)
    jer = JaccardErrorRate(collar=COLLAR_SEC, skip_overlap=False)(ref, hyp)
    return {"diarization_error_rate": round(float(der), 4),
            "jaccard_error_rate": round(float(jer), 4)}


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _name_at(system_segments, start: float, end: float) -> Dict[str, float]:
    """Seconds of [start, end) attributed to each system name."""
    by_name: Dict[str, float] = {}
    for seg in system_segments:
        o = _overlap(start, end, float(seg["start"]), float(seg["end"]))
        if o > 0:
            name = str(seg.get("speaker_label") or seg.get("speaker") or "UNKNOWN")
            by_name[name] = by_name.get(name, 0.0) + o
    return by_name


def identification(reference_turns, system_segments, enrolled: List[str]) -> Dict[str, Any]:
    enrolled_keys = {normalise(n) for n in enrolled}

    total = confused = unmatched = 0.0
    short_total = short_confused = 0.0

    for turn in reference_turns:
        ref_name = normalise(turn["speaker"])
        if ref_name not in enrolled_keys:
            continue
        start, end = float(turn["start"]), float(turn["end"])
        dur = end - start
        attributed = _name_at(system_segments, start, end)
        covered = sum(attributed.values())

        right = sum(sec for name, sec in attributed.items() if normalise(name) == ref_name)
        generic = sum(sec for name, sec in attributed.items() if _GENERIC.match(name.strip()))
        wrong = max(0.0, dur - right)   # uncovered speech counts as confusion too

        total += dur
        confused += wrong
        unmatched += generic
        if dur < SHORT_TURN_SEC:
            short_total += dur
            short_confused += wrong

    ratio = lambda n, d: None if d == 0 else round(n / d, 4)
    return {
        "enrolled_speech_sec": round(total, 2),
        "speaker_confusion_rate": ratio(confused, total),
        "short_turn_confusion_rate": ratio(short_confused, short_total),
        "unmatched_rate": ratio(unmatched, total),
    }
