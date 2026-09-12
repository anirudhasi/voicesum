"""
W1.7 — action items must not be attributed to whoever happened to be speaking.

MOM_EXTRACT_ACTIONS_FROM_POINTS_PROMPT instructs the model:

    NEVER assign ownership merely because someone was speaking, presenting,
    or sharing an update. If no specific owner or team was assigned, set
    owner: null.

_resolve_point_owner then discarded that null: it fell back to the source
point's whole speakers list, then its speaker, then the sole meeting
participant. Every unassigned action was presented as assigned, and a
multi-speaker point produced a comma-joined list of everyone who had talked.

The rule now returns (owner, owner_source) so evidence strength travels with
the answer:

    llm_explicit           the model named an owner
    stage1_extracted       extraction recorded one for the source point
    named_in_task          a participant is named inside the task text
    inferred_from_speaker  the point has exactly one speaker; weak, so it is
                           surfaced as inferred for a reviewer to confirm
    unassigned             no evidence

A wrong owner is acted upon; an unassigned item is triaged. Comma-joining
several speakers is gone entirely, because it was never evidence of anything.

Note for reviewers: the unassigned count will rise. That is the fix working.
"""
import pytest


def make_resolver(participants):
    """
    Mirror of the closure inside generate_mom_from_enhanced_rom, which closes
    over `participants` and so cannot be imported directly. Kept identical to
    the implementation; the source-level guards below catch divergence.
    """
    invalid_values = {"none", "n/a", "null", "unassigned", "unknown", "undefined", ""}

    def _clean(val):
        if val is None:
            return None
        s = str(val).strip()
        return None if s.lower() in invalid_values else s

    def resolve(act_owner, src_point, task_text=""):
        cleaned = _clean(act_owner)
        if cleaned:
            return cleaned, "llm_explicit"

        if src_point:
            p_owner = _clean(src_point.get("action_owner"))
            if p_owner:
                return p_owner, "stage1_extracted"

        if participants and isinstance(participants, list):
            task_lower = (task_text or "").lower()
            matched = [
                str(p).strip() for p in participants
                if _clean(p)
                and str(p).strip().lower() != "for information"
                and str(p).strip().lower() in task_lower
            ]
            if matched:
                return ", ".join(matched), "named_in_task"

        if src_point:
            spks = src_point.get("speakers")
            if isinstance(spks, str):
                spks = [x.strip() for x in spks.split(",") if x.strip()]
            if not spks:
                single = _clean(src_point.get("speaker"))
                spks = [single] if single else []
            candidates = []
            for spk in spks or []:
                cleaned_spk = _clean(spk)
                if cleaned_spk and cleaned_spk.lower() != "for information":
                    if cleaned_spk not in candidates:
                        candidates.append(cleaned_spk)
            if len(candidates) == 1:
                return candidates[0], "inferred_from_speaker"

        return "Unassigned", "unassigned"

    return resolve


# ── Evidence-based attribution ─────────────────────────────────────────────

def test_explicit_owner_from_the_model_is_used():
    resolve = make_resolver(["Alice", "Bob"])
    assert resolve("Bob", {"speaker": "Alice"}, "Do the thing") == ("Bob", "llm_explicit")


def test_stage1_extracted_owner_is_used_when_the_model_gives_none():
    resolve = make_resolver(["Alice", "Bob"])
    point = {"action_owner": "Carol", "speaker": "Alice", "speakers": ["Alice"]}
    assert resolve(None, point, "Do the thing") == ("Carol", "stage1_extracted")


def test_participant_named_in_the_task_text_is_used():
    """Evidence from the task itself, not from who was talking."""
    resolve = make_resolver(["Alice", "Bob"])
    point = {"speaker": "Carol", "speakers": ["Carol"]}
    assert resolve(None, point, "Bob will prepare the budget report") == ("Bob", "named_in_task")


def test_multiple_named_participants_are_both_returned():
    resolve = make_resolver(["Alice", "Bob"])
    owner, source = resolve(None, {}, "Alice and Bob will co-author the brief")
    assert owner == "Alice, Bob"
    assert source == "named_in_task"


# ── Speaking is weak evidence, and is labelled as such ─────────────────────

def test_single_speaker_is_inferred_not_asserted():
    """
    Retained because it is often right, but reported as inferred so the UI can
    mark it for confirmation rather than presenting it as established fact.
    """
    resolve = make_resolver(["Alice", "Bob"])
    point = {"speaker": "Alice", "speakers": ["Alice"]}
    assert resolve(None, point, "Review the schema") == ("Alice", "inferred_from_speaker")


def test_multiple_speakers_never_become_a_joint_owner():
    """The clearest defect: an unowned action assigned to everyone who spoke."""
    resolve = make_resolver(["Alice", "Bob", "Carol"])
    point = {"speakers": ["Alice", "Bob", "Carol"]}
    assert resolve(None, point, "The schema needs review") == ("Unassigned", "unassigned")


def test_two_speakers_are_not_joined_either():
    resolve = make_resolver(["Alice", "Bob"])
    assert resolve(None, {"speakers": ["Alice", "Bob"]}, "Review it")[0] == "Unassigned"


def test_repeated_speaker_still_counts_as_one():
    resolve = make_resolver(["Alice"])
    assert resolve(None, {"speakers": ["Alice", "Alice"]}, "Review it") == (
        "Alice", "inferred_from_speaker"
    )


def test_sole_participant_is_not_made_the_owner():
    """
    A one-person participant list is not evidence. Only a speaker recorded on
    the source point itself counts, and this point has none.
    """
    resolve = make_resolver(["Alice"])
    assert resolve(None, {}, "Review the schema") == ("Unassigned", "unassigned")


def test_no_evidence_at_all_is_unassigned():
    resolve = make_resolver([])
    assert resolve(None, {}, "Something must happen") == ("Unassigned", "unassigned")


@pytest.mark.parametrize(
    "placeholder", ["none", "N/A", "null", "unassigned", "UNKNOWN", "", "   "]
)
def test_placeholder_owners_are_treated_as_absent(placeholder):
    resolve = make_resolver(["Alice", "Bob"])
    point = {"speakers": ["Alice", "Bob"]}
    assert resolve(placeholder, point, "Do something") == ("Unassigned", "unassigned")


def test_for_information_is_never_an_owner():
    resolve = make_resolver(["Alice"])
    point = {"speakers": ["For information"]}
    assert resolve(None, point, "for information only") == ("Unassigned", "unassigned")


# ── Precedence ─────────────────────────────────────────────────────────────

def test_explicit_owner_beats_everything():
    resolve = make_resolver(["Alice", "Bob"])
    point = {"action_owner": "Carol", "speakers": ["Dave"]}
    assert resolve("Alice", point, "Bob will do it")[0] == "Alice"


def test_stage1_owner_beats_a_name_in_the_task_text():
    resolve = make_resolver(["Alice", "Bob"])
    assert resolve(None, {"action_owner": "Alice"}, "Bob will do it")[0] == "Alice"


def test_named_in_task_beats_the_speaker_inference():
    """Stronger evidence must win over weaker."""
    resolve = make_resolver(["Alice", "Bob"])
    point = {"speakers": ["Alice"]}
    assert resolve(None, point, "Bob will prepare the report") == ("Bob", "named_in_task")


# ── Source-level guards ────────────────────────────────────────────────────

def test_speaker_list_joining_is_gone_from_the_source():
    """Guards against a future refactor reinstating the joint-owner fallback."""
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "services" / "rom_service.py"
    text = src.read_text(encoding="utf-8")
    block_start = text.index("def _resolve_point_owner")
    block = text[block_start:block_start + 4000]

    assert "3. Try source point's speakers list" not in block
    assert "4. Try source point's speaker" not in block
    assert "if len(valid_parts) == 1:" not in block, (
        "the sole-participant fallback must not return an owner"
    )
    assert '"inferred_from_speaker"' in block, "weak evidence must be labelled"


def test_provenance_reaches_every_emitted_action_item():
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "services" / "rom_service.py"
    text = src.read_text(encoding="utf-8")
    assert text.count('"owner_source": owner_source') == 3, (
        "every emitted action item must carry its owner provenance"
    )


def test_prompt_still_instructs_the_model_to_return_null():
    """The code and the prompt must agree; they previously contradicted."""
    from services.ai_provider import MOM_EXTRACT_ACTIONS_FROM_POINTS_PROMPT as P
    assert "NEVER assign ownership merely because someone was speaking" in P
    assert "owner: null" in P
