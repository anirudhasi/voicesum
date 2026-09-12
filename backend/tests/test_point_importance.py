"""
W1.2 — importance scoring for discussion points.

From `Current issue and challenges.docx`: "How we can improve and assign
importance to the points".

Nothing in the system carried any measure of significance, so the instruction
to "keep the important point and summarize" had nothing behind it and the only
signals available to the model were ordinal position and text length. These
tests pin the scoring rule, and in particular its determinism, which is the
property that makes selection auditable.
"""
import pytest

from services import point_importance as imp


def _point(**kw):
    base = {
        "id": kw.pop("id", "p1"),
        "polished_text": kw.pop("text", "A routine status update was shared."),
        "speakers": [],
        "technical_terms": [],
        "dates": [],
        "numbers": [],
        "action_items": [],
        "original_point_ids": [],
        "timeline_start": 0.0,
        "timeline_end": 0.0,
    }
    base.update(kw)
    return base


# ── Individual signals ─────────────────────────────────────────────────────

def test_action_items_raise_the_score():
    plain = imp.score_point(_point())
    with_action = imp.score_point(_point(action_items=[{"task": "Do it"}]))
    assert with_action["score"] > plain["score"]
    assert with_action["signals"]["has_action_items"] == 1.0


@pytest.mark.parametrize("text", [
    "The committee agreed to proceed.",
    "It was decided that testing continues.",
    "The design was approved by the board.",
    "Vikas committed to the migration.",
    "The revised schedule must be met.",
    "The team will deliver by Friday.",
])
def test_decision_language_is_detected(text):
    assert imp.score_point(_point(text=text))["signals"]["decision_language"] == 1.0


@pytest.mark.parametrize("text", [
    "The weather was discussed briefly.",
    "Several options were mentioned.",
    "An undecided matter was noted.",
])
def test_ordinary_discussion_is_not_decision_language(text):
    assert imp.score_point(_point(text=text))["signals"]["decision_language"] == 0.0


def test_entity_density_rewards_specific_content():
    vague = imp.score_point(_point(text="We talked about the system at length today."))
    specific = imp.score_point(_point(
        text="FADEC calibration rose 18% by 12 March.",
        technical_terms=["FADEC"], numbers=["18%"], dates=["12 March"],
    ))
    assert specific["signals"]["entity_density"] > vague["signals"]["entity_density"]


def test_merge_weight_counts_points_merged_in():
    single = imp.score_point(_point(original_point_ids=["a"]))
    merged = imp.score_point(_point(original_point_ids=["a", "b", "c", "d"]))
    assert merged["signals"]["merge_weight"] > single["signals"]["merge_weight"]
    assert single["signals"]["merge_weight"] == 0.0, "one source is not a merge"


def test_speaker_breadth_counts_additional_speakers():
    one = imp.score_point(_point(speakers=["Alice"]))
    several = imp.score_point(_point(speakers=["Alice", "Bob", "Carol"]))
    assert several["signals"]["speaker_breadth"] > one["signals"]["speaker_breadth"]
    assert one["signals"]["speaker_breadth"] == 0.0


def test_duration_contributes():
    brief = imp.score_point(_point(timeline_start=0.0, timeline_end=5.0))
    long = imp.score_point(_point(timeline_start=0.0, timeline_end=280.0))
    assert long["signals"]["duration"] > brief["signals"]["duration"]


@pytest.mark.parametrize("confidence,expected", [
    ("high", 1.0), ("medium", 0.6), ("probable", 0.2), ("", 0.5), (None, 0.5),
])
def test_agenda_confidence_maps_to_a_score(confidence, expected):
    assert imp.score_point(_point(agenda_confidence=confidence))["signals"][
        "agenda_confidence"
    ] == expected


# ── Saturation, so no single signal dominates ──────────────────────────────

@pytest.mark.parametrize("signal,point", [
    ("speaker_breadth", _point(speakers=["a"] * 50)),
    ("merge_weight", _point(original_point_ids=["x"] * 50)),
    ("duration", _point(timeline_start=0.0, timeline_end=99999.0)),
    ("entity_density", _point(text="x", numbers=[str(i) for i in range(500)])),
])
def test_signals_saturate_at_one(signal, point):
    assert imp.score_point(point)["signals"][signal] == 1.0


def test_every_signal_is_within_range():
    point = _point(
        text="The team agreed to deliver FADEC recalibration by 12 March.",
        speakers=["a", "b"], technical_terms=["FADEC"], dates=["12 March"],
        action_items=[{"task": "t"}], original_point_ids=["x", "y"],
        timeline_start=0.0, timeline_end=120.0, agenda_confidence="high",
    )
    result = imp.score_point(point)
    assert 0.0 <= result["score"] <= 1.0
    for name, value in result["signals"].items():
        assert 0.0 <= value <= 1.0, f"{name} out of range: {value}"


def test_a_maximal_point_outranks_a_minimal_one():
    trivial = imp.score_point(_point(text="Coffee was mentioned."))
    substantial = imp.score_point(_point(
        text="The board approved the 18% FADEC budget increase on 12 March.",
        speakers=["a", "b", "c", "d"], technical_terms=["FADEC"],
        numbers=["18%"], dates=["12 March"], action_items=[{"task": "t"}],
        original_point_ids=["x", "y", "z", "w"],
        timeline_start=0.0, timeline_end=300.0, agenda_confidence="high",
    ))
    assert substantial["score"] > trivial["score"]
    assert substantial["score"] > 0.8


# ── Determinism: the reason selection left the model ───────────────────────

def test_scoring_is_deterministic():
    point = _point(
        text="The team agreed to proceed.", action_items=[{"task": "t"}],
        speakers=["a", "b"], timeline_start=0.0, timeline_end=90.0,
    )
    first = imp.score_point(point)
    for _ in range(10):
        assert imp.score_point(point) == first


def test_ranking_is_stable_for_equal_scores():
    """Ties must break on original position, never arbitrarily."""
    points = [_point(id=f"p{i}") for i in range(6)]
    order = [p["id"] for p in imp.rank(points)]
    for _ in range(5):
        assert [p["id"] for p in imp.rank(points)] == order
    assert order == [f"p{i}" for i in range(6)]


def test_selection_is_repeatable():
    points = [
        _point(id="p1", text="Routine note."),
        _point(id="p2", text="The team agreed.", action_items=[{"task": "t"}]),
        _point(id="p3", text="Another aside."),
    ]
    first = imp.select(points, 2)
    dropped = [p["id"] for p in first["dropped"]]
    for _ in range(5):
        assert [p["id"] for p in imp.select(points, 2)["dropped"]] == dropped


# ── Selection ──────────────────────────────────────────────────────────────

def test_selection_keeps_the_most_important():
    points = [
        _point(id="trivial", text="Coffee was mentioned."),
        _point(id="action", text="Bob agreed to file it.", action_items=[{"task": "t"}]),
        _point(id="aside", text="A brief digression occurred."),
    ]
    result = imp.select(points, 1)
    assert [p["id"] for p in result["selected"]] == ["action"]
    assert {p["id"] for p in result["dropped"]} == {"trivial", "aside"}


def test_selection_preserves_document_order():
    """Selection ranks; presentation stays chronological."""
    points = [
        _point(id="a", text="The board approved it.", action_items=[{"task": "t"}]),
        _point(id="b", text="Trivial remark."),
        _point(id="c", text="The team agreed.", action_items=[{"task": "t"}]),
    ]
    assert [p["id"] for p in imp.select(points, 2)["selected"]] == ["a", "c"]


def test_selection_accounts_for_every_point():
    points = [_point(id=f"p{i}") for i in range(7)]
    result = imp.select(points, 3)
    assert len(result["selected"]) == 3
    assert len(result["selected"]) + len(result["dropped"]) == len(points)


def test_target_at_or_above_input_drops_nothing():
    points = [_point(id=f"p{i}") for i in range(3)]
    for target in (3, 10):
        result = imp.select(points, target)
        assert len(result["selected"]) == 3 and result["dropped"] == []


def test_zero_target_drops_everything():
    points = [_point(id="a"), _point(id="b")]
    result = imp.select(points, 0)
    assert result["selected"] == [] and len(result["dropped"]) == 2


def test_empty_input_is_handled():
    assert imp.select([], 3) == {"selected": [], "dropped": []}
    assert imp.rank([]) == []
    assert imp.annotate([]) == []


# ── Weights ────────────────────────────────────────────────────────────────

def test_weights_change_the_ranking():
    """Tuning must be possible without editing code."""
    points = [
        _point(id="wordy", text="A long discussion about many things happened here today.",
               timeline_start=0.0, timeline_end=300.0),
        _point(id="actionable", text="Do it.", action_items=[{"task": "t"}]),
    ]
    action_first = imp.select(points, 1, weights={"has_action_items": 0.9, "duration": 0.01})
    duration_first = imp.select(points, 1, weights={"has_action_items": 0.01, "duration": 0.9})
    assert [p["id"] for p in action_first["selected"]] == ["actionable"]
    assert [p["id"] for p in duration_first["selected"]] == ["wordy"]


def test_unknown_weight_keys_are_ignored():
    assert imp.score_point(_point(), weights={"not_a_signal": 5.0})["score"] >= 0.0


def test_default_weights_sum_to_one():
    """Not required arithmetically, but keeps the score readable as a fraction."""
    assert round(sum(imp.DEFAULT_WEIGHTS.values()), 6) == 1.0


# ── Robustness: scoring must never break a ROM ─────────────────────────────

def test_annotate_survives_a_malformed_point():
    points = [_point(id="ok"), {"id": "broken", "speakers": "not-a-list"}, None]
    result = imp.annotate(points)
    assert result[0]["importance"]["score"] >= 0.0
    assert "importance" in result[1]


def test_missing_fields_score_without_error():
    assert 0.0 <= imp.score_point({"id": "bare"})["score"] <= 1.0


def test_alternate_text_fields_are_read():
    for field in ("polished_text", "enhanced_text", "text", "discussion_point"):
        point = {"id": "p", field: "The team agreed to proceed."}
        assert imp.score_point(point)["signals"]["decision_language"] == 1.0
