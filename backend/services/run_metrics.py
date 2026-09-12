"""
Run Metrics Service — one record per pipeline stage.

Design goals, following services/analytics.py:
  - Fully independent of the pipeline: any exception here is swallowed.
    Instrumentation must never be the reason a transcription fails.
  - One row per (run, stage) rather than flat columns on a job row, because
    the stage set differs between the single, chunked, rerun and ROM pipelines
    and a flat schema cannot extend to cover them.
  - Counters are accumulated through a context variable, so deep call sites
    (model loading, LLM calls) can report without their signatures changing.

This exists to answer questions that currently cannot be answered at all:
how long each stage takes, how many times a model is loaded within one run,
and how many tokens a stage actually spends. Those measurements gate the
efficiency work; none of it can be reported honestly without them.

`processing_analytics` remains the per-job summary. This table is the
per-stage detail beneath it.
"""
import contextvars
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# DB table definition (called from database.py connect_db)
# ─────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS run_metrics (
    id                  TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL,
    recording_id        TEXT,
    user_id             TEXT,

    stage               TEXT NOT NULL,
    started_at          TEXT NOT NULL,
    ended_at            TEXT,
    duration_sec        REAL,

    -- running | ok | failed | degraded
    outcome             TEXT NOT NULL DEFAULT 'running',
    error_message       TEXT,

    -- The counter that tests the model-reload hypothesis directly.
    model_load_count    INTEGER NOT NULL DEFAULT 0,
    llm_call_count      INTEGER NOT NULL DEFAULT 0,
    prompt_tokens       INTEGER NOT NULL DEFAULT 0,
    completion_tokens   INTEGER NOT NULL DEFAULT 0,
    retry_count         INTEGER NOT NULL DEFAULT 0,

    peak_vram_bytes     INTEGER,
    peak_rss_bytes      INTEGER,

    extra               TEXT NOT NULL DEFAULT '{}'
)
"""

ADD_INDEX_RUN_SQL = """
CREATE INDEX IF NOT EXISTS idx_run_metrics_run_id ON run_metrics(run_id)
"""

ADD_INDEX_RECORDING_SQL = """
CREATE INDEX IF NOT EXISTS idx_run_metrics_recording_id ON run_metrics(recording_id)
"""

ADD_INDEX_STAGE_SQL = """
CREATE INDEX IF NOT EXISTS idx_run_metrics_stage ON run_metrics(stage)
"""


# ─────────────────────────────────────────────────────────────
# In-flight stage state
# ─────────────────────────────────────────────────────────────

@dataclass
class StageRecord:
    """Counters accumulated while a stage runs."""
    run_id: str
    stage: str
    recording_id: Optional[str] = None
    user_id: Optional[str] = None

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: str = ""
    ended_at: Optional[str] = None
    duration_sec: Optional[float] = None
    outcome: str = "running"
    error_message: Optional[str] = None

    model_load_count: int = 0
    llm_call_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    retry_count: int = 0

    peak_vram_bytes: Optional[int] = None
    peak_rss_bytes: Optional[int] = None

    extra: Dict[str, Any] = field(default_factory=dict)

    def as_row(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "recording_id": self.recording_id,
            "user_id": self.user_id,
            "stage": self.stage,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_sec": self.duration_sec,
            "outcome": self.outcome,
            "error_message": self.error_message,
            "model_load_count": self.model_load_count,
            "llm_call_count": self.llm_call_count,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "retry_count": self.retry_count,
            "peak_vram_bytes": self.peak_vram_bytes,
            "peak_rss_bytes": self.peak_rss_bytes,
            "extra": json.dumps(self.extra, default=str),
        }


_current_stage: contextvars.ContextVar[Optional[StageRecord]] = contextvars.ContextVar(
    "run_metrics_current_stage", default=None
)


def current_stage() -> Optional[StageRecord]:
    """The stage in progress on this execution context, or None."""
    return _current_stage.get()


# ─────────────────────────────────────────────────────────────
# Reporting helpers — safe to call from anywhere
#
# Each is a no-op outside a stage, so call sites need no guard and
# instrumentation never becomes a source of failure.
# ─────────────────────────────────────────────────────────────

def note_model_load(model_name: str = "") -> None:
    """Record that a model was loaded into memory."""
    rec = _current_stage.get()
    if rec is None:
        return
    rec.model_load_count += 1
    if model_name:
        loads = rec.extra.setdefault("model_loads", {})
        loads[model_name] = loads.get(model_name, 0) + 1


def note_llm_call(prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    """Record one language-model call and what it spent."""
    rec = _current_stage.get()
    if rec is None:
        return
    rec.llm_call_count += 1
    rec.prompt_tokens += max(0, int(prompt_tokens or 0))
    rec.completion_tokens += max(0, int(completion_tokens or 0))


def note_retry(reason: str = "") -> None:
    rec = _current_stage.get()
    if rec is None:
        return
    rec.retry_count += 1
    if reason:
        rec.extra.setdefault("retry_reasons", []).append(reason)


def note_peak_memory(vram_bytes: Optional[int] = None, rss_bytes: Optional[int] = None) -> None:
    """Raise the recorded peaks. Only the high-water mark is kept."""
    rec = _current_stage.get()
    if rec is None:
        return
    if vram_bytes is not None:
        rec.peak_vram_bytes = max(rec.peak_vram_bytes or 0, int(vram_bytes))
    if rss_bytes is not None:
        rec.peak_rss_bytes = max(rec.peak_rss_bytes or 0, int(rss_bytes))


def note_detail(key: str, value: Any) -> None:
    """Attach an arbitrary detail to the stage record."""
    rec = _current_stage.get()
    if rec is None:
        return
    rec.extra[key] = value


def mark_degraded(reason: str) -> None:
    """
    Record that the stage completed but fell back or lost fidelity.

    Distinguishing degraded from ok is the difference between a run that
    succeeded and one that quietly produced less than it should have.
    """
    rec = _current_stage.get()
    if rec is None:
        return
    rec.outcome = "degraded"
    rec.extra.setdefault("degraded_reasons", []).append(reason)


def new_run_id() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────
# Stage context managers
# ─────────────────────────────────────────────────────────────

def _begin(run_id, stage, recording_id, user_id) -> tuple:
    rec = StageRecord(
        run_id=run_id or new_run_id(),
        stage=stage,
        recording_id=recording_id,
        user_id=user_id,
        started_at=_now(),
    )
    return rec, _current_stage.set(rec), time.perf_counter()


def _finish(rec: StageRecord, token, t0: float, exc: Optional[BaseException]) -> None:
    rec.duration_sec = round(time.perf_counter() - t0, 4)
    rec.ended_at = _now()
    if exc is not None:
        rec.outcome = "failed"
        rec.error_message = f"{type(exc).__name__}: {exc}"[:1000]
    elif rec.outcome == "running":
        rec.outcome = "ok"
    _current_stage.reset(token)


@contextmanager
def stage(stage_name: str, run_id: str = "", recording_id: str = None, user_id: str = None):
    """
    Time a stage synchronously. Yields the record so callers may inspect it.

    The record is not persisted here; use `persist_stage` or the async
    variant. Keeping timing separate from storage lets the pipeline be
    instrumented in places where no database session is available.
    """
    rec, token, t0 = _begin(run_id, stage_name, recording_id, user_id)
    try:
        yield rec
    except BaseException as exc:
        _finish(rec, token, t0, exc)
        raise
    else:
        _finish(rec, token, t0, None)


@asynccontextmanager
async def async_stage(
    stage_name: str,
    run_id: str = "",
    recording_id: str = None,
    user_id: str = None,
    persist: bool = True,
):
    """Time a stage and, by default, write the record when it ends."""
    rec, token, t0 = _begin(run_id, stage_name, recording_id, user_id)
    try:
        yield rec
    except BaseException as exc:
        _finish(rec, token, t0, exc)
        if persist:
            await persist_stage(rec)
        raise
    else:
        _finish(rec, token, t0, None)
        if persist:
            await persist_stage(rec)


# ─────────────────────────────────────────────────────────────
# Persistence — never raises
# ─────────────────────────────────────────────────────────────

INSERT_SQL = """
INSERT OR REPLACE INTO run_metrics (
    id, run_id, recording_id, user_id, stage,
    started_at, ended_at, duration_sec, outcome, error_message,
    model_load_count, llm_call_count, prompt_tokens, completion_tokens,
    retry_count, peak_vram_bytes, peak_rss_bytes, extra
) VALUES (
    :id, :run_id, :recording_id, :user_id, :stage,
    :started_at, :ended_at, :duration_sec, :outcome, :error_message,
    :model_load_count, :llm_call_count, :prompt_tokens, :completion_tokens,
    :retry_count, :peak_vram_bytes, :peak_rss_bytes, :extra
)
"""


async def persist_stage(rec: StageRecord) -> bool:
    """
    Write one stage record. Returns True on success.

    Swallows every exception: a metrics write must never break a pipeline.
    """
    try:
        from sqlalchemy import text
        from database import get_db_context

        async with get_db_context() as db:
            await db.execute(text(INSERT_SQL), rec.as_row())
            await db.commit()
        return True
    except Exception:
        logger.warning(
            "[RunMetrics] Could not persist stage '%s' for run %s",
            rec.stage, rec.run_id, exc_info=True,
        )
        return False


async def fetch_run(run_id: str) -> list:
    """Return every stage record for a run, in start order."""
    try:
        from sqlalchemy import text
        from database import get_db_context

        async with get_db_context() as db:
            r = await db.execute(
                text(
                    "SELECT * FROM run_metrics WHERE run_id = :rid "
                    "ORDER BY started_at ASC"
                ),
                {"rid": run_id},
            )
            return [dict(row) for row in r.mappings()]
    except Exception:
        logger.warning("[RunMetrics] Could not read run %s", run_id, exc_info=True)
        return []


def summarize(rows: list) -> Dict[str, Any]:
    """
    Reduce stage rows to the per-run figures the plan calls for.

    `total_duration_sec` sums the stages rather than taking wall clock, so
    that a run is attributable: the parts add up to the whole, and a slow run
    points at a stage.
    """
    if not rows:
        return {
            "stages": 0, "total_duration_sec": 0.0, "model_load_count": 0,
            "llm_call_count": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "retry_count": 0, "failed_stages": [], "degraded_stages": [],
            "peak_vram_bytes": None,
        }

    def total(key):
        return sum(int(r.get(key) or 0) for r in rows)

    vrams = [int(r["peak_vram_bytes"]) for r in rows if r.get("peak_vram_bytes")]
    return {
        "stages": len(rows),
        "total_duration_sec": round(sum(float(r.get("duration_sec") or 0.0) for r in rows), 4),
        "model_load_count": total("model_load_count"),
        "llm_call_count": total("llm_call_count"),
        "prompt_tokens": total("prompt_tokens"),
        "completion_tokens": total("completion_tokens"),
        "retry_count": total("retry_count"),
        "failed_stages": [r["stage"] for r in rows if r.get("outcome") == "failed"],
        "degraded_stages": [r["stage"] for r in rows if r.get("outcome") == "degraded"],
        "peak_vram_bytes": max(vrams) if vrams else None,
    }
