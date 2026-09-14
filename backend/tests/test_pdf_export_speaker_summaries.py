"""
PDF export must succeed for recordings that have per-speaker summaries.

Section 7 (per-speaker summaries) read the speaker colour map, but the map was
only built in Section 8, after it. Every export of a recording with the
speaker-summary option enabled raised UnboundLocalError and produced no PDF.
Found by static analysis (ruff F821), confirmed here by actually building one.
"""
import pytest

pytest.importorskip("reportlab")

from routers.pdf_router import _build_pdf  # noqa: E402


def _recording():
    return {
        "filename": "propulsion_review.wav",
        "created_at": "2026-09-14T10:00:00+00:00",
        "duration": 95.0,
        "status": "done",
        "summary": "Propulsion review.",
        "short_summary": "Short.",
        "detailed_summary": "Detailed.",
        "key_points": ["FADEC recalibrated"],
        "action_items": ["Arjun to circulate the calibration report"],
        "speakers_detected": ["Arjun", "Meera"],
        "transcript": [
            {"start": 0.0, "end": 40.0, "speaker_label": "Arjun", "text": "FADEC margin rose 18 percent."},
            {"start": 40.0, "end": 95.0, "speaker_label": "Meera", "text": "LRU-4 vibration exceeded limits."},
        ],
    }


EXEC = {"overview": "Engine health review.", "key_outcomes": ["Recalibration agreed"]}


def test_pdf_builds_with_per_speaker_summaries():
    """The case that crashed."""
    pdf = _build_pdf(
        _recording(), EXEC, ["Replace the LRU-4 bracket"],
        speaker_summary_data={
            "Arjun": {"summary": "Led the FADEC discussion.", "key_points": ["18% margin"], "action_items": []},
            "Meera": {"summary": "Reported vibration.", "key_points": ["2.4 g"], "action_items": []},
        },
    )
    assert pdf[:5] == b"%PDF-", "output is not a PDF"
    assert len(pdf) > 2000


def test_pdf_builds_without_per_speaker_summaries():
    """The previously working path must keep working."""
    pdf = _build_pdf(_recording(), EXEC, [])
    assert pdf[:5] == b"%PDF-"


def test_pdf_builds_with_empty_transcript_and_summaries():
    """No speakers means an empty colour map; Section 7 must fall back, not crash."""
    rec = _recording()
    rec["transcript"] = []
    pdf = _build_pdf(rec, EXEC, [], speaker_summary_data={"Arjun": {"summary": "x"}})
    assert pdf[:5] == b"%PDF-"
