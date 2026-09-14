"""
Golden dataset format.

A dataset is a directory with a manifest.json listing cases. Two kinds of case
exist, because the two halves of the system need different ground truth:

audio  A real recording with reference speaker turns and a reference
       transcript. Scores transcription, diarization and speaker
       identification. Ground truth must come from a human listening to the
       recording; there is no way to synthesise it credibly.

rom    A set of Stage 2 discussion points with labelled must-keep content and
       reference action items. Scores information retention in short and
       medium ROM versions and action-point extraction. These can be authored
       from real meetings or constructed, and every case records which.

Provenance is a required field so that a report can never quietly mix
constructed cases with customer data and present the result as representative.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

Provenance = Literal["real", "synthetic"]


# ── Audio cases ────────────────────────────────────────────────────────────

class ReferenceTurn(BaseModel):
    """One stretch of speech by one person, as heard by the labeller."""
    start: float = Field(ge=0.0)
    end: float = Field(gt=0.0)
    speaker: str = Field(min_length=1, description="Real name, or 'UNKNOWN' if unenrolled")

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, v, info):
        start = info.data.get("start")
        if start is not None and v <= start:
            raise ValueError(f"turn end {v} must be after start {start}")
        return v


class AudioReference(BaseModel):
    turns: List[ReferenceTurn]
    transcript: str = Field(description="Verbatim reference transcript, all speakers")
    enrolled_speakers: List[str] = Field(
        default_factory=list,
        description="Speakers with a voice profile; others are expected to be unmatched",
    )


# ── ROM cases ──────────────────────────────────────────────────────────────

class ReferenceAction(BaseModel):
    task: str = Field(min_length=3)
    owner: Optional[str] = Field(
        default=None,
        description="Assigned owner, or null when genuinely unassigned. A correct null counts as correct.",
    )
    source_point_id: Optional[str] = None
    superseded: bool = Field(
        default=False,
        description="Commitment later withdrawn or reassigned; should not survive as stated",
    )


class RomReference(BaseModel):
    must_keep_point_ids: List[str] = Field(
        default_factory=list,
        description="Points a reviewer requires in the SHORT version",
    )
    must_keep_entities: List[str] = Field(
        default_factory=list,
        description="Dates, figures and technical terms that must survive condensation",
    )
    actions: List[ReferenceAction] = Field(default_factory=list)


# ── Manifest ───────────────────────────────────────────────────────────────

class Case(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_\-]+$")
    kind: Literal["audio", "rom"]
    provenance: Provenance
    description: str = ""
    # Relative to the dataset directory.
    audio_file: Optional[str] = None
    reference_file: str
    input_file: Optional[str] = Field(
        default=None, description="For rom cases: the Stage 2 ROM to condense"
    )

    @field_validator("audio_file")
    @classmethod
    def _audio_needs_file(cls, v, info):
        if info.data.get("kind") == "audio" and not v:
            raise ValueError("audio cases require audio_file")
        return v


class Manifest(BaseModel):
    name: str
    version: str
    cases: List[Case]

    @field_validator("cases")
    @classmethod
    def _unique_ids(cls, v):
        ids = [c.id for c in v]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate case ids: {sorted(dupes)}")
        return v


LOCAL_MANIFEST = "manifest.local.json"


def load_manifest(dataset_dir: Path) -> Manifest:
    """
    Load manifest.json, then merge manifest.local.json if present.

    manifest.json is tracked and holds only cases safe to commit. Real
    recordings, and the references and transcripts derived from them, are
    sensitive and ignored by git, so the cases that point at them live in the
    untracked manifest.local.json. A fresh clone therefore never lists a case
    whose files it does not have.
    """
    dataset_dir = Path(dataset_dir)
    data = json.loads((dataset_dir / "manifest.json").read_text(encoding="utf-8"))

    local = dataset_dir / LOCAL_MANIFEST
    if local.is_file():
        extra = json.loads(local.read_text(encoding="utf-8"))
        data["cases"] = list(data.get("cases", [])) + list(extra.get("cases", []))

    return Manifest.model_validate(data)


class DraftReferenceError(ValueError):
    """Raised when a case points at a reference nobody has corrected."""


def load_reference(dataset_dir: Path, case: Case):
    path = Path(dataset_dir) / case.reference_file
    raw = json.loads(path.read_text(encoding="utf-8"))

    # eval.prelabel writes drafts from the system's own output. Scoring against
    # an uncorrected draft compares the system with itself, which always reads
    # as perfect and measures nothing, so a draft is refused outright.
    status = str(raw.get("_status", "")) if isinstance(raw, dict) else ""
    if "draft" in path.name.lower() or status.upper().startswith("DRAFT"):
        raise DraftReferenceError(
            f"{path} is an uncorrected draft. Correct it by listening to the "
            "recording, remove the _status field, and rename it to reference.json."
        )

    return AudioReference.model_validate(raw) if case.kind == "audio" else RomReference.model_validate(raw)
