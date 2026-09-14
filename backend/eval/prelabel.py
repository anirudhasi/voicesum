"""
Bootstrap golden-dataset reference files from a processed recording.

Labelling a meeting from scratch takes hours. Correcting the system's own
output takes a fraction of that, so this exports a recording's transcript as a
draft reference for a human to correct, plus the system transcript the runner
scores against.

    python -m eval.prelabel --recording-id <id> --case-id standup_0914 --dataset eval/golden

Writes:
    <dataset>/audio/<case-id>/reference.draft.json   draft for a human to correct
    <dataset>/system/<case-id>.json                  the system output as-is

Rename reference.draft.json to reference.json only after a person has listened
to the recording and corrected every turn boundary, speaker name and word. An
uncorrected draft scores the system against itself, which always reads as
perfect and measures nothing. The runner refuses to load a draft for that
reason.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path


def _db_path() -> Path:
    from config import settings

    return Path(settings.DATABASE_URL.split("///", 1)[1])


def export(recording_id: str, case_id: str, dataset: Path, enrolled: list[str]) -> int:
    conn = sqlite3.connect(_db_path())
    row = conn.execute(
        "SELECT file_path, transcript FROM recordings WHERE id = ?", (recording_id,)
    ).fetchone()
    if not row:
        print(f"recording {recording_id} not found", file=sys.stderr)
        return 1

    file_path, transcript_json = row
    segments = json.loads(transcript_json or "[]")
    if not segments:
        print("recording has no transcript yet; let the pipeline finish first", file=sys.stderr)
        return 1

    case_dir = dataset / "audio" / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    audio_src = Path(file_path)
    audio_dst = case_dir / f"audio{audio_src.suffix}"
    if audio_src.is_file() and not audio_dst.exists():
        shutil.copy2(audio_src, audio_dst)

    draft = {
        "_status": "DRAFT: correct every turn, name and word, then rename to reference.json",
        "enrolled_speakers": enrolled,
        "transcript": " ".join(str(s.get("text", "")).strip() for s in segments),
        "turns": [
            {
                "start": round(float(s["start"]), 2),
                "end": round(float(s["end"]), 2),
                "speaker": s.get("speaker_label") or s.get("speaker") or "UNKNOWN",
                "_system_text": s.get("text", ""),
            }
            for s in segments
            if float(s.get("end", 0)) > float(s.get("start", 0))
        ],
    }
    (case_dir / "reference.draft.json").write_text(json.dumps(draft, indent=2), encoding="utf-8")

    system_dir = dataset / "system"
    system_dir.mkdir(parents=True, exist_ok=True)
    (system_dir / f"{case_id}.json").write_text(json.dumps(segments, indent=2), encoding="utf-8")

    print(f"Draft reference: {case_dir / 'reference.draft.json'}")
    print(f"System output:   {system_dir / (case_id + '.json')}")
    print("Correct the draft by listening to the audio, rename it to reference.json,")
    print("then add the case to manifest.json with provenance \"real\".")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Export a recording as a draft golden case")
    parser.add_argument("--recording-id", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--dataset", default="eval/golden", type=Path)
    parser.add_argument("--enrolled", default="", help="comma-separated enrolled speaker names")
    args = parser.parse_args(argv)
    enrolled = [n.strip() for n in args.enrolled.split(",") if n.strip()]
    return export(args.recording_id, args.case_id, args.dataset, enrolled)


if __name__ == "__main__":
    sys.exit(main())
