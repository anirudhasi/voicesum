"""
W1.4 — the speaker refinement pass must be able to fire.

Reported symptom: "some edge cases of wrong speaker assignment".

Root cause: in refine_transcript_speakers_with_ecapa, original_label_sim was
initialised to 1.0 and only replaced when the current label matched an enrolled
profile or a conversation centroid. A segment carrying a generic "Speaker N"
label matches neither, so the value stayed at 1.0 and the override test

    (best_profile_sim - original_label_sim) > speaker_refinement_margin

became (best - 1.0) > 0.30, which no cosine similarity in [-1, 1] can satisfy.
Only the outright accept threshold could rescue such a segment, and that sits
above the matching threshold. The population most likely to be mislabelled was
therefore the one population the correction pass could not touch.

These tests pin the decision rule itself, which is pure arithmetic, rather than
running the audio pipeline.
"""
import pytest

from config import settings


ACCEPT = settings.SPEAKER_REFINEMENT_ACCEPT_THRESHOLD
FLOOR = settings.SPEAKER_REFINEMENT_MIN_SIMILARITY
MARGIN = settings.speaker_refinement_margin


def should_override(best_sim, original_sim, margin=MARGIN,
                    accept=ACCEPT, floor=FLOOR):
    """The override rule exactly as implemented in identification.py."""
    return bool(
        best_sim >= accept
        or (best_sim > floor and (best_sim - original_sim) > margin)
    )


# ── The defect, stated as a test ───────────────────────────────────────────

def test_unmeasured_original_no_longer_blocks_every_override():
    """
    With the old initialisation of 1.0, no value of best_sim could satisfy the
    margin branch. With 0.0 a confident alternative wins.
    """
    unmeasured = 0.0
    assert should_override(best_sim=0.55, original_sim=unmeasured) is True

    stale = 1.0   # the old behaviour
    assert should_override(best_sim=0.55, original_sim=stale) is False


@pytest.mark.parametrize("best_sim", [0.25, 0.40, 0.55, 0.70, 0.81, 0.95, 1.0])
def test_margin_branch_was_unsatisfiable_under_the_old_initialisation(best_sim):
    """
    No similarity at all could clear (best - 1.0) > margin, which is why the
    pass never corrected a generic label.

    Parenthesised deliberately: `a > b is False` chains in Python and would
    silently test something else.
    """
    assert ((best_sim - 1.0) > MARGIN) is False


# ── The rule itself ────────────────────────────────────────────────────────

def test_accept_threshold_overrides_regardless_of_the_original():
    """A very confident match wins even against a strong current label."""
    assert should_override(best_sim=ACCEPT, original_sim=0.99) is True
    assert should_override(best_sim=ACCEPT + 0.05, original_sim=1.0) is True


def test_just_below_accept_falls_through_to_the_margin_branch():
    just_below = ACCEPT - 0.01
    assert should_override(best_sim=just_below, original_sim=0.99) is False
    assert should_override(best_sim=just_below, original_sim=0.10) is True


def test_floor_blocks_weak_alternatives():
    """A near-noise match must not displace a label, even an unmeasured one."""
    assert should_override(best_sim=FLOOR, original_sim=0.0) is False
    assert should_override(best_sim=FLOOR - 0.01, original_sim=0.0) is False


def test_clearing_the_floor_is_not_sufficient_on_its_own():
    """
    Both conditions must hold: above the floor, and beating the current label
    by more than the margin. Against an unmeasured original (0.0) that makes
    the margin the binding constraint.
    """
    assert FLOOR < MARGIN, "with FLOOR below MARGIN, the margin binds"
    assert should_override(best_sim=FLOOR + 0.01, original_sim=0.0) is False
    assert should_override(best_sim=MARGIN + 0.01, original_sim=0.0) is True


def test_margin_must_be_exceeded_not_merely_met():
    original = 0.40
    assert should_override(best_sim=original + MARGIN, original_sim=original) is False
    assert should_override(best_sim=original + MARGIN + 0.001, original_sim=original) is True


def test_a_confident_current_label_is_not_displaced_by_a_marginal_rival():
    """The pass must correct mistakes without destabilising good assignments."""
    assert should_override(best_sim=0.70, original_sim=0.69) is False


def test_larger_margin_is_more_conservative():
    assert should_override(best_sim=0.60, original_sim=0.20, margin=0.30) is True
    assert should_override(best_sim=0.60, original_sim=0.20, margin=0.50) is False


# ── Configuration ──────────────────────────────────────────────────────────

def test_thresholds_are_configurable():
    """
    These were hard-coded literals sitting beside an already-configurable
    margin, so tuning one silently invalidated the others.
    """
    assert hasattr(settings, "SPEAKER_REFINEMENT_ACCEPT_THRESHOLD")
    assert hasattr(settings, "SPEAKER_REFINEMENT_MIN_SIMILARITY")


def test_defaults_are_unchanged_from_the_shipped_values():
    """This change fixes a bug; it must not silently retune the system."""
    assert ACCEPT == 0.82
    assert FLOOR == 0.20
    assert MARGIN == 0.30


def test_accept_threshold_sits_above_the_matching_threshold():
    """
    Overriding an existing label is a stronger claim than matching one, so the
    accept threshold should not fall below the identification threshold.
    """
    assert ACCEPT >= settings.SPEAKER_SIMILARITY_THRESHOLD_ECAPA_TDNN


def test_no_hardcoded_literals_remain_in_the_override_rule():
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "services" / "identification.py"
    text = src.read_text(encoding="utf-8")
    assert "best_profile_sim >= accept_threshold" in text
    assert "best_profile_sim > min_similarity" in text
    assert "best_profile_sim >= 0.82" not in text
    assert "best_profile_sim > 0.20" not in text


def test_unmeasured_original_is_set_to_zero_in_source():
    """Guards the fix itself against a future refactor restoring 1.0."""
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "services" / "identification.py"
    text = src.read_text(encoding="utf-8")
    assert "original_label_sim = 0.0" in text, (
        "an unmeasured original label must score 0.0, not 1.0"
    )
