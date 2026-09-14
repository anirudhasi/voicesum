"""
The golden dataset itself must be valid, and the harness must refuse to
produce misleading numbers.
"""
import json
from pathlib import Path

import pytest

from eval.metrics import rom as rom_metrics
from eval.runner import aggregate, render_markdown
from eval.schema import Case, DraftReferenceError, load_manifest, load_reference

GOLDEN = Path(__file__).resolve().parent.parent / "eval" / "golden"


def test_manifest_loads():
    m = load_manifest(GOLDEN)
    assert m.cases, "golden dataset has no cases"


def test_every_case_declares_provenance():
    for case in load_manifest(GOLDEN).cases:
        assert case.provenance in ("real", "synthetic"), case.id


@pytest.mark.parametrize("case", load_manifest(GOLDEN).cases, ids=lambda c: c.id)
def test_every_reference_file_is_valid(case):
    load_reference(GOLDEN, case)


@pytest.mark.parametrize(
    "case", [c for c in load_manifest(GOLDEN).cases if c.kind == "rom"], ids=lambda c: c.id
)
def test_rom_input_contains_every_referenced_point(case):
    """A must-keep id that is not in the input can never be kept; the case would be wrong."""
    ref = load_reference(GOLDEN, case)
    source = json.loads((GOLDEN / case.input_file).read_text(encoding="utf-8"))
    ids = {p["id"] for a in source["agendas"] for p in a["discussion_points"]}
    assert set(ref.must_keep_point_ids) <= ids
    for action in ref.actions:
        if action.source_point_id:
            assert action.source_point_id in ids, action.task


@pytest.mark.parametrize(
    "case", [c for c in load_manifest(GOLDEN).cases if c.kind == "rom"], ids=lambda c: c.id
)
def test_uncondensed_input_retains_every_entity(case):
    """
    Control: the long version must already contain every must-keep entity in
    its metadata. If it does not, the reference asks for something the input
    never had, and a low score would blame the system for a labelling error.
    """
    ref = load_reference(GOLDEN, case)
    source = json.loads((GOLDEN / case.input_file).read_text(encoding="utf-8"))
    r = rom_metrics.entity_retention(source, ref.must_keep_entities)
    assert r["entity_retention_metadata"] == 1.0, r["missing_from_metadata"]
    assert r["entity_retention_text"] == 1.0, r["missing_from_text"]


def test_draft_reference_is_refused(tmp_path):
    """An uncorrected draft scores the system against itself: always perfect, meaningless."""
    (tmp_path / "audio").mkdir()
    draft = tmp_path / "audio" / "reference.draft.json"
    draft.write_text(json.dumps({
        "_status": "DRAFT: correct every turn",
        "transcript": "x", "turns": [{"start": 0, "end": 1, "speaker": "A"}],
    }), encoding="utf-8")
    case = Case(id="c", kind="audio", provenance="real",
                audio_file="audio/a.wav", reference_file="audio/reference.draft.json")
    with pytest.raises(DraftReferenceError):
        load_reference(tmp_path, case)


def test_draft_status_is_refused_even_if_renamed(tmp_path):
    ref = tmp_path / "reference.json"
    ref.write_text(json.dumps({
        "_status": "DRAFT: not yet corrected",
        "transcript": "x", "turns": [{"start": 0, "end": 1, "speaker": "A"}],
    }), encoding="utf-8")
    case = Case(id="c", kind="audio", provenance="real",
                audio_file="a.wav", reference_file="reference.json")
    with pytest.raises(DraftReferenceError):
        load_reference(tmp_path, case)


def test_synthetic_only_report_carries_a_warning():
    report = {
        "environment": {"generated_at": "t", "commit": "c", "language_model": "m",
                        "settings": {"EMBEDDING_MODEL": "e"}, "settings_hash": "h"},
        "results": [{"id": "x", "kind": "rom", "provenance": "synthetic"}],
        "aggregate": aggregate([]),
    }
    md = render_markdown(report)
    assert "Every case in this report is synthetic" in md


def test_mixed_report_has_no_synthetic_only_warning():
    report = {
        "environment": {"generated_at": "t", "commit": "c", "language_model": "m",
                        "settings": {"EMBEDDING_MODEL": "e"}, "settings_hash": "h"},
        "results": [{"id": "x", "kind": "rom", "provenance": "synthetic"},
                    {"id": "y", "kind": "audio", "provenance": "real", "skipped": "none"}],
        "aggregate": aggregate([]),
    }
    assert "Every case in this report is synthetic" not in render_markdown(report)


def test_aggregate_counts_only_measured_cases():
    results = [
        {"short": {"entity_retention_text": 1.0}},
        {"short": {"entity_retention_text": 0.5}},
        {"short": {"entity_retention_text": None}},
    ]
    agg = aggregate(results)["Short: entities in text"]
    assert agg == {"mean": 0.75, "n": 2}


def test_local_manifest_cases_are_merged(tmp_path):
    """Real cases live in the untracked manifest.local.json and must be picked up."""
    (tmp_path / "manifest.json").write_text(json.dumps({
        "name": "t", "version": "1", "cases": [
            {"id": "synthetic_one", "kind": "rom", "provenance": "synthetic",
             "input_file": "i.json", "reference_file": "r.json"}]}), encoding="utf-8")
    (tmp_path / "manifest.local.json").write_text(json.dumps({
        "cases": [{"id": "real_one", "kind": "audio", "provenance": "real",
                   "audio_file": "a.wav", "reference_file": "ref.json"}]}), encoding="utf-8")
    ids = [c.id for c in load_manifest(tmp_path).cases]
    assert ids == ["synthetic_one", "real_one"]


def test_tracked_manifest_contains_no_real_cases():
    """Real meeting data must never be referenced from the tracked manifest."""
    tracked = json.loads((GOLDEN / "manifest.json").read_text(encoding="utf-8"))
    assert all(c["provenance"] == "synthetic" for c in tracked["cases"])


def test_duplicate_ids_across_manifests_are_rejected(tmp_path):
    case = {"id": "dup", "kind": "rom", "provenance": "synthetic",
            "input_file": "i.json", "reference_file": "r.json"}
    (tmp_path / "manifest.json").write_text(json.dumps({"name": "t", "version": "1", "cases": [case]}), encoding="utf-8")
    (tmp_path / "manifest.local.json").write_text(json.dumps({"cases": [case]}), encoding="utf-8")
    with pytest.raises(Exception):
        load_manifest(tmp_path)
