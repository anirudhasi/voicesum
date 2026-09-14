"""
The evaluation harness's metrics must mean exactly what they say.

A wrong metric is worse than none: it produces confident numbers that point
engineering effort in the wrong direction. These tests pin each definition
against hand-computed cases.
"""
import pytest

from eval.metrics import actions, rom, speech
from eval.metrics.text import contains_entity, normalise, token_jaccard


# ── Normalisation ──────────────────────────────────────────────────────────

def test_normalise_strips_case_and_punctuation():
    assert normalise("The FADEC, Recalibrated!") == "the fadec recalibrated"


def test_normalise_keeps_decimal_points_in_figures():
    """Figures are the content that must not be lost."""
    assert "18.5" in normalise("Thrust rose 18.5% in March.")


def test_normalise_strips_accents():
    assert normalise("Café résumé") == "cafe resume"


def test_entity_match_respects_token_boundaries():
    """18% must not be found inside 118%."""
    assert contains_entity("margin rose 18%", "18%")
    assert not contains_entity("margin rose 118%", "18%")


def test_entity_match_is_case_and_punctuation_insensitive():
    assert contains_entity("Deadline: 12 March.", "12 march")


def test_token_jaccard_ignores_function_words():
    assert token_jaccard("prepare the budget report", "prepare budget report") == 1.0


# ── Information retention (G2) ─────────────────────────────────────────────

def _version(points, coverage=None):
    agenda = {"discussion_points": points}
    if coverage is not None:
        agenda["condensation_coverage"] = coverage
    return {"agendas": [agenda]}


def test_entity_retention_distinguishes_text_from_metadata():
    """
    Facts kept in metadata but written out of the prose are lost to a reader.
    The two must never be reported as one number.
    """
    v = _version([{
        "polished_text": "FADEC recalibration was agreed.",
        "technical_terms": ["FADEC"], "dates": ["12 March"], "numbers": ["18%"],
    }])
    r = rom.entity_retention(v, ["FADEC", "12 March", "18%"])
    assert r["entity_retention_metadata"] == 1.0
    assert r["entity_retention_text"] == round(1 / 3, 4)
    assert set(r["missing_from_text"]) == {"12 March", "18%"}


def test_entity_retention_with_no_entities_is_undefined_not_perfect():
    """No reference entities means no measurement, not 100%."""
    assert rom.entity_retention(_version([]), [])["entity_retention_text"] is None


def test_must_keep_recall_uses_provenance_not_text():
    """A well-paraphrased point still counts as kept."""
    v = _version(
        [{"polished_text": "Completely reworded.", "source_point_ids": ["p1"]}],
        coverage={"retained_point_ids": ["p1", "p2"]},
    )
    r = rom.must_keep_recall(v, ["p1", "p2", "p3"])
    assert r["must_keep_recall"] == round(2 / 3, 4)
    assert r["must_keep_dropped"] == ["p3"]


# ── Action extraction (G3) ─────────────────────────────────────────────────

REF = [
    {"task": "Prepare the Q3 budget report", "owner": "Bob"},
    {"task": "Recalibrate the FADEC unit", "owner": None},
    {"task": "Book the review hall", "owner": "Carol", "superseded": True},
]


def test_perfect_extraction():
    sys = [
        {"task": "Prepare Q3 budget report", "owner": "Bob"},
        {"task": "Recalibrate FADEC unit", "owner": "Unassigned"},
    ]
    s = actions.score(sys, REF)
    assert s["action_precision"] == 1.0 and s["action_recall"] == 1.0
    assert s["owner_accuracy"] == 1.0


def test_correct_null_owner_counts_as_correct():
    """Rewarding any non-null owner would reward the defect being fixed."""
    sys = [{"task": "Recalibrate FADEC unit", "owner": None}]
    s = actions.score(sys, REF)
    assert s["owner_accuracy"] == 1.0
    assert s["non_null_owner_precision"] is None   # nothing was named


def test_guessed_owner_is_penalised():
    sys = [{"task": "Recalibrate FADEC unit", "owner": "Alice", "owner_source": "inferred_from_speaker"}]
    s = actions.score(sys, REF)
    assert s["owner_accuracy"] == 0.0
    assert s["non_null_owner_precision"] == 0.0
    assert s["owner_errors"][0]["owner_source"] == "inferred_from_speaker"


def test_co_owner_order_does_not_matter():
    ref = [{"task": "Co-author the design brief", "owner": "Alice, Bob"}]
    sys = [{"task": "Co-author design brief", "owner": "Bob and Alice"}]
    assert actions.score(sys, ref)["owner_accuracy"] == 1.0


def test_matching_is_one_to_one():
    """One verbose action must not be credited against two references."""
    ref = [{"task": "Prepare budget report"}, {"task": "Prepare budget summary"}]
    sys = [{"task": "Prepare budget report and summary"}]
    s = actions.score(sys, ref)
    assert s["matched"] == 1
    assert s["action_recall"] == 0.5


def test_unrelated_action_does_not_match():
    sys = [{"task": "Order new chairs for the canteen", "owner": "Dave"}]
    s = actions.score(sys, REF)
    assert s["matched"] == 0
    assert s["action_precision"] == 0.0


def test_superseded_actions_are_excluded_and_flagged():
    sys = [{"task": "Book the review hall", "owner": "Carol"}]
    s = actions.score(sys, REF)
    assert s["reference_actions"] == 2          # superseded one excluded
    assert s["superseded_emitted"] == 1


def test_correct_replacement_is_not_flagged_as_superseded():
    """
    A commitment reassigned later in the meeting: the correct final action
    closely resembles the superseded one and must not be counted as both.
    """
    ref = [
        {"task": "Book the altitude test chamber for 5 April", "owner": "Meera"},
        {"task": "Book the altitude test chamber for 2 April", "owner": "Kiran", "superseded": True},
    ]
    sys = [{"task": "Book altitude test chamber for 5 April", "owner": "Meera"}]
    s = actions.score(sys, ref)
    assert s["matched"] == 1
    assert s["superseded_emitted"] == 0


def test_no_system_actions_gives_undefined_precision():
    s = actions.score([], REF)
    assert s["action_precision"] is None
    assert s["action_recall"] == 0.0


# ── Speaker identification (G1) ────────────────────────────────────────────

REF_TURNS = [
    {"start": 0.0, "end": 10.0, "speaker": "Alice"},
    {"start": 10.0, "end": 12.0, "speaker": "Bob"},     # short turn
    {"start": 12.0, "end": 20.0, "speaker": "Bob"},
]


def test_perfect_identification():
    sys = [
        {"start": 0.0, "end": 10.0, "speaker_label": "Alice"},
        {"start": 10.0, "end": 20.0, "speaker_label": "Bob"},
    ]
    r = speech.identification(REF_TURNS, sys, enrolled=["Alice", "Bob"])
    assert r["speaker_confusion_rate"] == 0.0
    assert r["short_turn_confusion_rate"] == 0.0
    assert r["unmatched_rate"] == 0.0


def test_short_interjection_misattributed_by_proximity():
    """The reported edge case: Bob's short reply labelled as Alice."""
    sys = [
        {"start": 0.0, "end": 12.0, "speaker_label": "Alice"},
        {"start": 12.0, "end": 20.0, "speaker_label": "Bob"},
    ]
    r = speech.identification(REF_TURNS, sys, enrolled=["Alice", "Bob"])
    assert r["speaker_confusion_rate"] == round(2 / 20, 4)
    assert r["short_turn_confusion_rate"] == 1.0


def test_generic_label_counts_as_unmatched_and_confused():
    sys = [
        {"start": 0.0, "end": 10.0, "speaker_label": "Alice"},
        {"start": 10.0, "end": 20.0, "speaker_label": "Speaker 2"},
    ]
    r = speech.identification(REF_TURNS, sys, enrolled=["Alice", "Bob"])
    assert r["unmatched_rate"] == 0.5
    assert r["speaker_confusion_rate"] == 0.5


def test_unenrolled_reference_speakers_are_not_scored():
    ref = [{"start": 0.0, "end": 5.0, "speaker": "Visitor"}]
    sys = [{"start": 0.0, "end": 5.0, "speaker_label": "Speaker 1"}]
    r = speech.identification(ref, sys, enrolled=["Alice"])
    assert r["speaker_confusion_rate"] is None


def test_uncovered_speech_counts_as_confusion():
    """Speech the system dropped entirely cannot count as correct."""
    ref = [{"start": 0.0, "end": 10.0, "speaker": "Alice"}]
    sys = [{"start": 0.0, "end": 5.0, "speaker_label": "Alice"}]
    r = speech.identification(ref, sys, enrolled=["Alice"])
    assert r["speaker_confusion_rate"] == 0.5


def test_wer_uses_shared_normalisation():
    pytest.importorskip("jiwer")
    assert speech.word_error_rate("The FADEC was recalibrated.", "the fadec was recalibrated") == 0.0
    assert speech.word_error_rate("one two three four", "one two three") == 0.25


def test_der_is_zero_for_identical_turns_under_relabelling():
    """DER applies an optimal label mapping, so names do not matter."""
    pytest.importorskip("pyannote.metrics")
    sys = [
        {"start": 0.0, "end": 10.0, "speaker": "SPEAKER_00"},
        {"start": 10.0, "end": 20.0, "speaker": "SPEAKER_01"},
    ]
    r = speech.diarization_errors(REF_TURNS, sys)
    assert r["diarization_error_rate"] == 0.0
