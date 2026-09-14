"""
Run the golden dataset and write a report.

    python -m eval.runner --dataset eval/golden --out eval/reports/latest
    python -m eval.runner --dataset eval/golden --out eval/reports/x --cases budget_schedule
    python -m eval.runner --dataset eval/golden --out eval/reports/x --system-dir exports/

ROM cases run the real condensation and action extraction through the
configured language model. Audio cases are scored against a system transcript
exported from the application (see eval/prelabel.py), because running the full
transcription pipeline needs a database recording and is covered separately.

The report states the provenance of every case and flags a report built only
from synthetic cases, so a number from constructed data can never be presented
as a measurement of real meetings.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional

from eval.metrics import actions as action_metrics
from eval.metrics import rom as rom_metrics
from eval.metrics import speech as speech_metrics
from eval.schema import Case, load_manifest, load_reference

VERSIONS = ("short", "medium")


# ── Environment capture ────────────────────────────────────────────────────

def _git_commit() -> Optional[str]:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return None


def _environment() -> Dict[str, Any]:
    from config import settings

    relevant = {
        k: getattr(settings, k, None)
        for k in (
            "EMBEDDING_MODEL", "OLLAMA_MODEL_PRIORITY", "WHISPER_MODEL_SIZE",
            "WHISPER_COMPUTE_TYPE", "RAG_CHUNK_SIZE", "SPEAKER_SIMILARITY_THRESHOLD_ECAPA_TDNN",
            "SPEAKER_REFINEMENT_ACCEPT_THRESHOLD", "speaker_refinement_margin",
        )
    }
    settings_hash = hashlib.sha256(
        json.dumps(relevant, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]

    llm = None
    try:
        from services.ai_provider import QwenProvider
        use, url, _, priority = QwenProvider._get_ollama_settings()
        if use:
            llm = QwenProvider._detect_ollama_model(url, priority)
    except Exception:
        pass

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "commit": _git_commit(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "language_model": llm,
        "settings": relevant,
        "settings_hash": settings_hash,
    }


# ── ROM cases ──────────────────────────────────────────────────────────────

def _flatten_points(rom: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        p for a in rom.get("agendas", []) for p in a.get("discussion_points", [])
        if isinstance(p, dict)
    ]


def run_rom_case(dataset: Path, case: Case, skip_actions: bool) -> Dict[str, Any]:
    from services.rom_service import RomService

    reference = load_reference(dataset, case)
    source = json.loads((dataset / case.input_file).read_text(encoding="utf-8"))
    service = RomService()
    result: Dict[str, Any] = {"id": case.id, "kind": "rom", "provenance": case.provenance}

    # Control: the uncondensed input. Metadata retention here should be total;
    # if it is not, the reference and the input disagree and the case is wrong.
    result["long"] = rom_metrics.entity_retention(source, reference.must_keep_entities)

    for version in VERSIONS:
        started = time.perf_counter()
        error = None
        try:
            condensed = service.generate_rom_version(copy.deepcopy(source), version)
        except Exception as exc:
            condensed, error = {}, f"{type(exc).__name__}: {exc}"
        elapsed = round(time.perf_counter() - started, 2)

        block = {"elapsed_sec": elapsed, "error": error}
        block.update(rom_metrics.entity_retention(condensed, reference.must_keep_entities))
        if version == "short":
            block.update(rom_metrics.must_keep_recall(condensed, reference.must_keep_point_ids))
        block["output_points"] = len(_flatten_points(condensed))
        block["input_points"] = len(_flatten_points(source))
        result[version] = block

    if not skip_actions:
        started = time.perf_counter()
        error, system_actions = None, []
        try:
            mom = service.generate_mom_from_enhanced_rom(
                polished_points=copy.deepcopy(_flatten_points(source)),
                recording_meta={"filename": case.id},
            )
            system_actions = mom.get("action_items", []) or []
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        block = action_metrics.score(
            system_actions, [a.model_dump() for a in reference.actions]
        )
        block["elapsed_sec"] = round(time.perf_counter() - started, 2)
        block["error"] = error
        result["actions"] = block

    return result


# ── Audio cases ────────────────────────────────────────────────────────────

def run_audio_case(dataset: Path, case: Case, system_dir: Optional[Path]) -> Dict[str, Any]:
    reference = load_reference(dataset, case)
    result: Dict[str, Any] = {"id": case.id, "kind": "audio", "provenance": case.provenance}

    system_file = (system_dir / f"{case.id}.json") if system_dir else None
    if not system_file or not system_file.is_file():
        result["skipped"] = (
            f"no system transcript at {system_file}; export one with eval.prelabel"
        )
        return result

    segments = json.loads(system_file.read_text(encoding="utf-8"))
    hypothesis = " ".join(str(s.get("text", "")) for s in segments)
    turns = [t.model_dump() for t in reference.turns]

    result["word_error_rate"] = speech_metrics.word_error_rate(reference.transcript, hypothesis)
    result.update(speech_metrics.diarization_errors(turns, segments))
    result.update(speech_metrics.identification(turns, segments, reference.enrolled_speakers))
    return result


# ── Aggregation and reporting ──────────────────────────────────────────────

AGGREGATES = [
    ("short", "entity_retention_text", "Short: entities in text"),
    ("short", "entity_retention_metadata", "Short: entities in metadata"),
    ("short", "must_keep_recall", "Short: must-keep recall"),
    ("medium", "entity_retention_text", "Medium: entities in text"),
    ("medium", "entity_retention_metadata", "Medium: entities in metadata"),
    ("actions", "action_precision", "Action precision"),
    ("actions", "action_recall", "Action recall"),
    ("actions", "owner_accuracy", "Owner accuracy (correct null counts)"),
    ("actions", "non_null_owner_precision", "Named-owner precision"),
    (None, "word_error_rate", "Word error rate"),
    (None, "diarization_error_rate", "Diarization error rate"),
    (None, "speaker_confusion_rate", "Speaker confusion rate"),
    (None, "short_turn_confusion_rate", "Short-turn confusion rate"),
]


def _values(results, section, key):
    out = []
    for r in results:
        block = r.get(section, {}) if section else r
        v = block.get(key) if isinstance(block, dict) else None
        if isinstance(v, (int, float)):
            out.append(float(v))
    return out


def aggregate(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    agg = {}
    for section, key, label in AGGREGATES:
        vals = _values(results, section, key)
        agg[label] = {"mean": round(mean(vals), 4) if vals else None, "n": len(vals)}
    return agg


def _fmt(v) -> str:
    return "n/a" if v is None else f"{v:.2f}"


def render_markdown(report: Dict[str, Any]) -> str:
    env, results = report["environment"], report["results"]
    provenances = {r["provenance"] for r in results}
    lines = ["# Golden dataset evaluation", ""]

    if provenances == {"synthetic"}:
        lines += [
            "> **Every case in this report is synthetic.** These numbers measure the",
            "> system against constructed meetings. They are not a measurement of",
            "> performance on real recordings and must not be quoted as one.",
            "",
        ]

    lines += [
        "| | |", "|---|---|",
        f"| Generated | {env['generated_at']} |",
        f"| Commit | {env.get('commit') or 'unknown'} |",
        f"| Language model | {env.get('language_model') or 'unknown'} |",
        f"| Embedding model | {env['settings'].get('EMBEDDING_MODEL')} |",
        f"| Settings hash | {env['settings_hash']} |",
        f"| Cases | {len(results)} ({', '.join(sorted(provenances))}) |",
        "", "## Aggregate", "",
        "| Metric | Mean | Cases |", "|---|---|---|",
    ]
    for label, v in report["aggregate"].items():
        if v["n"]:
            lines.append(f"| {label} | {_fmt(v['mean'])} | {v['n']} |")

    lines += ["", "## Per case", ""]
    for r in results:
        lines.append(f"### {r['id']} ({r['provenance']})")
        lines.append("")
        if r.get("skipped"):
            lines += [f"Skipped: {r['skipped']}", ""]
            continue
        if r["kind"] == "rom":
            lines += ["| Version | Points | Entities in text | In metadata | Must-keep | Seconds |",
                      "|---|---|---|---|---|---|"]
            for v in ("long",) + VERSIONS:
                b = r.get(v, {})
                pts = f"{b.get('input_points', '')}→{b.get('output_points', '')}" if v != "long" else ""
                lines.append(
                    f"| {v} | {pts} | {_fmt(b.get('entity_retention_text'))} | "
                    f"{_fmt(b.get('entity_retention_metadata'))} | "
                    f"{_fmt(b.get('must_keep_recall'))} | {b.get('elapsed_sec', '')} |"
                )
            for v in VERSIONS:
                b = r.get(v, {})
                if b.get("error"):
                    lines.append(f"\n{v} error: `{b['error']}`")
                if b.get("missing_from_text"):
                    lines.append(f"\n{v} lost from text: {', '.join(b['missing_from_text'])}")
                if b.get("must_keep_dropped"):
                    lines.append(f"\n{v} dropped must-keep points: {', '.join(b['must_keep_dropped'])}")
            a = r.get("actions")
            if a:
                lines += ["", "| Actions | Precision | Recall | Owner acc. | Named-owner prec. | Superseded emitted |",
                          "|---|---|---|---|---|---|",
                          f"| {a['matched']}/{a['reference_actions']} matched | {_fmt(a['action_precision'])} | "
                          f"{_fmt(a['action_recall'])} | {_fmt(a['owner_accuracy'])} | "
                          f"{_fmt(a['non_null_owner_precision'])} | {a['superseded_emitted']} |"]
                if a.get("error"):
                    lines.append(f"\nactions error: `{a['error']}`")
                for e in a.get("owner_errors", []):
                    lines.append(f"\n- owner wrong on \"{e['task']}\": expected `{e['expected']}`, "
                                 f"got `{e['got']}` ({e.get('owner_source') or 'no source'})")
                for m in a.get("missed", []):
                    lines.append(f"\n- missed: \"{m}\"")
        else:
            for k in ("word_error_rate", "diarization_error_rate", "jaccard_error_rate",
                      "speaker_confusion_rate", "short_turn_confusion_rate", "unmatched_rate"):
                lines.append(f"- {k}: {_fmt(r.get(k))}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the golden dataset")
    parser.add_argument("--dataset", default="eval/golden", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--cases", default="", help="comma-separated case ids")
    parser.add_argument("--system-dir", type=Path, default=None,
                        help="directory of exported system transcripts for audio cases")
    parser.add_argument("--skip-actions", action="store_true")
    args = parser.parse_args(argv)

    manifest = load_manifest(args.dataset)
    wanted = {c.strip() for c in args.cases.split(",") if c.strip()}
    cases = [c for c in manifest.cases if not wanted or c.id in wanted]
    if not cases:
        print("no matching cases", file=sys.stderr)
        return 2

    env = _environment()
    print(f"Evaluating {len(cases)} case(s) with language model: {env.get('language_model')}")

    results = []
    for case in cases:
        print(f"  {case.id} ({case.kind}, {case.provenance}) ...", flush=True)
        started = time.perf_counter()
        if case.kind == "rom":
            results.append(run_rom_case(args.dataset, case, args.skip_actions))
        else:
            results.append(run_audio_case(args.dataset, case, args.system_dir))
        print(f"    done in {time.perf_counter() - started:.1f}s", flush=True)

    report = {
        "dataset": {"name": manifest.name, "version": manifest.version},
        "environment": env,
        "results": results,
        "aggregate": aggregate(results),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    (args.out / "report.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"Report written to {args.out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
