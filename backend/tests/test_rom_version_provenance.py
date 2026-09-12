"""
W1.1 — short and medium ROM versions must not lose information.

The reported defect: metadata was reattached to condensed points by list
position, after a prompt that explicitly asks the model to merge and reorder,
and technical terms, dates and numbers were discarded outright. Both are
addressed by carrying provenance through the condensation and unioning the
metadata of the sources behind each output point.

This is the defect behind the Aeronautical Development Agency feedback in
`Current issue and challenges.docx`.
"""
import json

import pytest

from services.rom_service import RomService


def _point(pid, text, **kw):
    base = {
        "id": pid,
        "polished_text": text,
        "speaker": kw.get("speaker", "Alice"),
        "speakers": kw.get("speakers", ["Alice"]),
        "action_owner": kw.get("action_owner"),
        "action_items": kw.get("action_items", []),
        "references": kw.get("references", []),
        "technical_terms": kw.get("technical_terms", []),
        "dates": kw.get("dates", []),
        "numbers": kw.get("numbers", []),
        "timeline_start": kw.get("timeline_start", 0.0),
        "timeline_end": kw.get("timeline_end", 0.0),
    }
    return base


# ── Parsing provenance out of the model response ───────────────────────────

def test_parses_objects_with_source_ids():
    raw = json.dumps([
        {"text": "Merged point.", "source_point_ids": ["p1", "p3"]},
        {"text": "Second point.", "source_point_ids": ["p2"]},
    ])
    items = RomService._parse_condensed_items(raw)
    assert [i["text"] for i in items] == ["Merged point.", "Second point."]
    assert items[0]["source_point_ids"] == ["p1", "p3"]


def test_parses_through_think_tags_and_fences():
    raw = (
        "<think>deciding what to keep</think>\n"
        "```json\n"
        '[{"text": "A.", "source_point_ids": ["p1"]}]\n'
        "```"
    )
    items = RomService._parse_condensed_items(raw)
    assert items == [{"text": "A.", "source_point_ids": ["p1"]}]


def test_tolerates_trailing_commas():
    raw = '[{"text": "A.", "source_point_ids": ["p1"]},]'
    assert RomService._parse_condensed_items(raw)[0]["text"] == "A."


def test_accepts_alternative_id_field_names():
    """Stage 2 dedup already uses original_point_ids; accept that spelling too."""
    raw = '[{"text": "A.", "original_point_ids": ["p1", "p2"]}]'
    assert RomService._parse_condensed_items(raw)[0]["source_point_ids"] == ["p1", "p2"]


def test_single_id_as_string_is_accepted():
    raw = '[{"text": "A.", "source_point_ids": "p1"}]'
    assert RomService._parse_condensed_items(raw)[0]["source_point_ids"] == ["p1"]


def test_bare_strings_still_parse_with_unknown_provenance():
    """A model that ignores the contract must still produce a usable document."""
    items = RomService._parse_condensed_items('["Point one.", "Point two."]')
    assert [i["text"] for i in items] == ["Point one.", "Point two."]
    assert all(i["source_point_ids"] == [] for i in items)


def test_bullet_list_fallback():
    items = RomService._parse_condensed_items("1. First point\n2. Second point")
    assert [i["text"] for i in items] == ["First point", "Second point"]


def test_empty_response_yields_nothing():
    assert RomService._parse_condensed_items("") == []
    assert RomService._parse_condensed_items(None) == []


# ── Metadata union: the actual information-loss fix ────────────────────────

def test_entities_from_every_source_survive():
    """
    The core assertion. A point merged from three sources concerns all three
    sources' entities; dropping any is the reported loss.
    """
    sources = [
        _point("p1", "a", technical_terms=["FADEC"], dates=["12 March"], numbers=["18%"]),
        _point("p2", "b", technical_terms=["AMCA"], dates=["April 8"], numbers=["4200"]),
        _point("p3", "c", technical_terms=["FADEC", "LRU"], dates=[], numbers=["7"]),
    ]
    merged = RomService._merge_source_metadata(sources)
    assert merged["technical_terms"] == ["FADEC", "AMCA", "LRU"]   # deduped, ordered
    assert merged["dates"] == ["12 March", "April 8"]
    assert merged["numbers"] == ["18%", "4200", "7"]


def test_action_items_from_every_source_survive():
    sources = [
        _point("p1", "a", action_items=[{"task": "Audit GPU use", "owner": "Bob"}]),
        _point("p2", "b", action_items=[{"task": "File the report", "owner": "Alice"}]),
    ]
    merged = RomService._merge_source_metadata(sources)
    assert len(merged["action_items"]) == 2
    assert {i["task"] for i in merged["action_items"]} == {"Audit GPU use", "File the report"}


def test_identical_action_items_are_deduplicated():
    item = {"task": "Audit GPU use", "owner": "Bob"}
    merged = RomService._merge_source_metadata([
        _point("p1", "a", action_items=[item]),
        _point("p2", "b", action_items=[dict(item)]),
    ])
    assert len(merged["action_items"]) == 1


def test_speakers_are_unioned_in_order():
    merged = RomService._merge_source_metadata([
        _point("p1", "a", speakers=["Alice", "Bob"]),
        _point("p2", "b", speakers=["Bob", "Carol"]),
    ])
    assert merged["speakers"] == ["Alice", "Bob", "Carol"]


def test_timeline_spans_all_sources():
    merged = RomService._merge_source_metadata([
        _point("p1", "a", timeline_start=120.0, timeline_end=180.0),
        _point("p2", "b", timeline_start=10.0, timeline_end=60.0),
    ])
    assert merged["timeline_start"] == 10.0
    assert merged["timeline_end"] == 180.0


def test_first_non_empty_action_owner_wins():
    merged = RomService._merge_source_metadata([
        _point("p1", "a", action_owner=None),
        _point("p2", "b", action_owner="Bob"),
    ])
    assert merged["action_owner"] == "Bob"


def test_merge_of_a_single_source_is_lossless():
    src = _point(
        "p1", "a", technical_terms=["FADEC"], dates=["12 March"],
        numbers=["18%"], references=["DOC-1"], action_items=[{"task": "t"}],
    )
    merged = RomService._merge_source_metadata([src])
    for field in ("technical_terms", "dates", "numbers", "references", "action_items"):
        assert merged[field] == src[field], f"{field} was lost"


def test_merge_with_no_sources_uses_the_fallback():
    fallback = _point("p9", "a", technical_terms=["X"])
    assert RomService._merge_source_metadata([], fallback)["technical_terms"] == ["X"]


def test_merge_is_deterministic():
    """Repeated runs must produce identical output for identical input."""
    sources = [
        _point("p1", "a", technical_terms=["B", "A"], numbers=["2"]),
        _point("p2", "b", technical_terms=["A", "C"], numbers=["1"]),
    ]
    first = RomService._merge_source_metadata(sources)
    for _ in range(5):
        assert RomService._merge_source_metadata(sources) == first


# ── The regression this replaces ───────────────────────────────────────────

def test_metadata_follows_content_not_position():
    """
    Direct regression guard. The model reorders, so output index 2 must take
    its metadata from whichever input it names, not from input index 2.
    """
    pts = [
        _point("p1", "first", action_owner="Alice", technical_terms=["ALPHA"]),
        _point("p2", "second", action_owner="Bob", technical_terms=["BETA"]),
        _point("p3", "third", action_owner="Carol", technical_terms=["GAMMA"]),
    ]
    by_id = {p["id"]: p for p in pts}

    # The model emits one output, derived from the *third* input.
    item = {"text": "Condensed.", "source_point_ids": ["p3"]}
    sources = [by_id[i] for i in item["source_point_ids"]]
    merged = RomService._merge_source_metadata(sources)

    assert merged["action_owner"] == "Carol"
    assert merged["technical_terms"] == ["GAMMA"]


def test_more_outputs_than_inputs_no_longer_collapse():
    """
    Previously every output beyond the input count reused the last input.
    With provenance each output resolves independently.
    """
    pts = [_point("p1", "a", action_owner="Alice"), _point("p2", "b", action_owner="Bob")]
    by_id = {p["id"]: p for p in pts}
    items = [
        {"text": "x", "source_point_ids": ["p1"]},
        {"text": "y", "source_point_ids": ["p2"]},
        {"text": "z", "source_point_ids": ["p1", "p2"]},
    ]
    owners = [
        RomService._merge_source_metadata([by_id[i] for i in it["source_point_ids"]])["action_owner"]
        for it in items
    ]
    assert owners == ["Alice", "Bob", "Alice"]


# ── End to end through the real generate_rom_version ───────────────────────

class _StubProvider:
    """Returns a canned model response; records the prompts it was given."""
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def query(self, prompt, max_tokens=None, temperature=None):
        self.prompts.append(prompt)
        return self.response

    def unload_model(self):
        pass


@pytest.fixture
def rom_with_stub(monkeypatch):
    def _install(response):
        stub = _StubProvider(response)
        monkeypatch.setattr("services.ai_provider.get_provider", lambda: stub)
        # Use the real prompt templates. A simplified stub would not catch a
        # template that breaks str.format(), which is exactly how the JSON
        # example in the provenance contract first went wrong.
        from services.ai_provider import (
            ROM_VERSION_SHORT_PROMPT,
            ROM_VERSION_MEDIUM_PROMPT,
        )
        real = {
            "rom_version_short": ROM_VERSION_SHORT_PROMPT,
            "rom_version_medium": ROM_VERSION_MEDIUM_PROMPT,
        }
        monkeypatch.setattr(
            "services.prompt_service.get_prompt_sync", lambda key: real[key]
        )
        return stub
    return _install


def test_real_prompt_templates_survive_formatting():
    """
    Regression guard. The prompts are rendered with str.format(), so a literal
    brace in the JSON example is read as a placeholder unless it is doubled.
    """
    from services.ai_provider import (
        ROM_VERSION_SHORT_PROMPT,
        ROM_VERSION_MEDIUM_PROMPT,
    )
    for template in (ROM_VERSION_SHORT_PROMPT, ROM_VERSION_MEDIUM_PROMPT):
        rendered = template.format(
            agenda_title="Propulsion", points_json="[Point 1] ID=p1", rules_section=""
        )
        assert "source_point_ids" in rendered
        assert '"text": "point 1"' in rendered, "the JSON example must survive"
        assert "{{" not in rendered and "}}" not in rendered


def _rom():
    return {
        "agendas": [{
            "agenda_id": "A1",
            "title": "Propulsion review",
            "discussion_points": [
                _point("p1", "FADEC calibration discussed.",
                       technical_terms=["FADEC"], dates=["12 March"], numbers=["18%"],
                       action_owner="Alice", speakers=["Alice"],
                       timeline_start=0.0, timeline_end=120.0,
                       action_items=[{"task": "Recalibrate FADEC", "owner": "Alice"}]),
                _point("p2", "AMCA integration timeline.",
                       technical_terms=["AMCA"], dates=["April 8"], numbers=["4200"],
                       action_owner="Bob", speakers=["Bob"],
                       timeline_start=120.0, timeline_end=240.0),
                _point("p3", "Minor scheduling aside.",
                       technical_terms=[], dates=[], numbers=[], speakers=["Carol"],
                       timeline_start=240.0, timeline_end=300.0),
            ],
        }]
    }


def test_end_to_end_preserves_entities_from_merged_sources(rom_with_stub):
    rom_with_stub(json.dumps([
        {"text": "FADEC recalibration and AMCA integration were agreed.",
         "source_point_ids": ["p1", "p2"]},
    ]))

    out = RomService().generate_rom_version(_rom(), "short")
    pt = out["agendas"][0]["discussion_points"][0]

    assert pt["technical_terms"] == ["FADEC", "AMCA"]
    assert pt["dates"] == ["12 March", "April 8"]
    assert pt["numbers"] == ["18%", "4200"]
    assert pt["speakers"] == ["Alice", "Bob"]
    assert pt["timeline_start"] == 0.0 and pt["timeline_end"] == 240.0
    assert pt["action_items"] == [{"task": "Recalibrate FADEC", "owner": "Alice"}]
    assert pt["source_point_ids"] == ["p1", "p2"]


def test_end_to_end_records_coverage_including_drops(rom_with_stub):
    rom_with_stub(json.dumps([
        {"text": "Combined.", "source_point_ids": ["p1", "p2"]},
    ]))

    out = RomService().generate_rom_version(_rom(), "short")
    coverage = out["agendas"][0]["condensation_coverage"]

    assert coverage["version"] == "short"
    assert coverage["input_point_ids"] == ["p1", "p2", "p3"]
    assert coverage["retained_point_ids"] == ["p1", "p2"]
    assert coverage["dropped_point_ids"] == ["p3"], "a dropped point must be visible"


def test_end_to_end_original_is_not_mutated(rom_with_stub):
    rom_with_stub(json.dumps([{"text": "X.", "source_point_ids": ["p1"]}]))
    original = _rom()
    RomService().generate_rom_version(original, "short")
    assert len(original["agendas"][0]["discussion_points"]) == 3
    assert "condensation_coverage" not in original["agendas"][0]


def test_end_to_end_falls_back_to_position_without_provenance(rom_with_stub):
    """A model ignoring the contract must still yield a usable document."""
    rom_with_stub(json.dumps(["First condensed.", "Second condensed."]))
    out = RomService().generate_rom_version(_rom(), "medium")
    points = out["agendas"][0]["discussion_points"]
    assert [p["text"] for p in points] == ["First condensed.", "Second condensed."]
    assert points[0]["technical_terms"] == ["FADEC"]


def test_end_to_end_unparseable_response_keeps_originals(rom_with_stub):
    rom_with_stub("the model said something useless")
    out = RomService().generate_rom_version(_rom(), "short")
    assert len(out["agendas"][0]["discussion_points"]) == 3


def test_unknown_source_ids_are_ignored_not_fatal(rom_with_stub):
    """A hallucinated id must not crash the run."""
    rom_with_stub(json.dumps([
        {"text": "Combined.", "source_point_ids": ["p1", "does-not-exist"]},
    ]))
    out = RomService().generate_rom_version(_rom(), "short")
    pt = out["agendas"][0]["discussion_points"][0]
    assert pt["source_point_ids"] == ["p1"]
    assert pt["technical_terms"] == ["FADEC"]


def test_long_version_is_untouched(rom_with_stub):
    rom_with_stub(json.dumps([{"text": "X.", "source_point_ids": ["p1"]}]))
    out = RomService().generate_rom_version(_rom(), "long")
    assert len(out["agendas"][0]["discussion_points"]) == 3


def test_prompt_carries_the_point_ids(rom_with_stub):
    """The model cannot cite ids it was never shown."""
    stub = rom_with_stub(json.dumps([{"text": "X.", "source_point_ids": ["p1"]}]))
    RomService().generate_rom_version(_rom(), "short")
    assert "ID=p1" in stub.prompts[0]
    assert "ID=p2" in stub.prompts[0]


# ── Importance scoring reaches the condensation (W1.2) ─────────────────────

def test_importance_is_scored_and_shown_to_the_model(rom_with_stub):
    """
    Without this the prompt's instruction to keep the important points has
    nothing behind it: the model sees only order and length.
    """
    stub = rom_with_stub(json.dumps([{"text": "X.", "source_point_ids": ["p1"]}]))
    RomService().generate_rom_version(_rom(), "short")
    prompt = stub.prompts[0]
    assert "Importance=" in prompt, "importance must be visible to the model"
    assert "ID=p1 | " in prompt


def test_importance_is_attached_to_the_input_points(rom_with_stub):
    rom_with_stub(json.dumps([{"text": "X.", "source_point_ids": ["p1"]}]))
    rom = _rom()
    RomService().generate_rom_version(rom, "short")
    # generate_rom_version deep-copies, so scoring lands on the copy; assert
    # via the prompt instead that a real score was produced.
    assert rom["agendas"][0]["discussion_points"][0].get("importance") is None


def test_action_bearing_point_scores_above_an_aside(rom_with_stub):
    """The ordering the scorer is meant to produce, on realistic input."""
    from services.point_importance import score_point
    rom = _rom()
    pts = {p["id"]: p for p in rom["agendas"][0]["discussion_points"]}
    action_pt = score_point(pts["p1"])["score"]
    aside_pt = score_point(pts["p3"])["score"]
    assert action_pt > aside_pt


def test_condensation_still_works_when_scoring_fails(rom_with_stub, monkeypatch):
    """Scoring is advisory; a failure must not stop a ROM being produced."""
    def _boom(_points, weights=None):
        raise RuntimeError("scorer unavailable")
    monkeypatch.setattr("services.point_importance.annotate", _boom)

    rom_with_stub(json.dumps([{"text": "X.", "source_point_ids": ["p1"]}]))
    out = RomService().generate_rom_version(_rom(), "short")
    assert out["agendas"][0]["discussion_points"][0]["text"] == "X."
