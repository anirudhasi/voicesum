"""
W0.2 — per-stage instrumentation.

These measurements gate the efficiency work: model load count tests the
model-reload hypothesis directly, and no latency or token claim can be made
without a stage breakdown. The service must therefore be correct, and must
never be able to break a pipeline.
"""
import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from services import run_metrics as rm


# ── Counters accumulate onto the active stage ──────────────────────────────

def test_stage_records_duration_and_outcome():
    with rm.stage("transcription", run_id="r1", recording_id="rec1") as rec:
        pass
    assert rec.outcome == "ok"
    assert rec.duration_sec is not None and rec.duration_sec >= 0
    assert rec.started_at and rec.ended_at


def test_counters_accumulate():
    with rm.stage("stage2", run_id="r1") as rec:
        rm.note_model_load("qwen3-4b")
        rm.note_model_load("qwen3-4b")
        rm.note_llm_call(prompt_tokens=100, completion_tokens=40)
        rm.note_llm_call(prompt_tokens=50, completion_tokens=10)
        rm.note_retry("json parse failure")
    assert rec.model_load_count == 2
    assert rec.extra["model_loads"] == {"qwen3-4b": 2}
    assert rec.llm_call_count == 2
    assert rec.prompt_tokens == 150
    assert rec.completion_tokens == 50
    assert rec.retry_count == 1
    assert rec.extra["retry_reasons"] == ["json parse failure"]


def test_peak_memory_keeps_the_high_water_mark():
    with rm.stage("transcription", run_id="r1") as rec:
        rm.note_peak_memory(vram_bytes=1000)
        rm.note_peak_memory(vram_bytes=5000)
        rm.note_peak_memory(vram_bytes=2000)   # must not lower the peak
    assert rec.peak_vram_bytes == 5000


def test_negative_token_counts_are_clamped():
    with rm.stage("s", run_id="r1") as rec:
        rm.note_llm_call(prompt_tokens=-5, completion_tokens=None)
    assert rec.prompt_tokens == 0 and rec.completion_tokens == 0


def test_degraded_is_distinct_from_ok():
    """A stage that fell back succeeded, but not fully; the two must not merge."""
    with rm.stage("rom_version", run_id="r1") as rec:
        rm.mark_degraded("LLM output unparseable, kept originals")
    assert rec.outcome == "degraded"
    assert rec.extra["degraded_reasons"] == ["LLM output unparseable, kept originals"]


def test_failure_is_recorded_and_exception_still_propagates():
    with pytest.raises(ValueError):
        with rm.stage("diarization", run_id="r1") as rec:
            raise ValueError("pyannote unavailable")
    assert rec.outcome == "failed"
    assert "ValueError: pyannote unavailable" in rec.error_message
    assert rec.duration_sec is not None


# ── Reporting outside a stage must be harmless ─────────────────────────────

def test_helpers_are_noops_outside_a_stage():
    """
    Call sites must not need a guard. Instrumentation that can raise would
    become a source of pipeline failure, which defeats its purpose.
    """
    assert rm.current_stage() is None
    rm.note_model_load("whisper")
    rm.note_llm_call(10, 10)
    rm.note_retry("x")
    rm.note_peak_memory(vram_bytes=1)
    rm.note_detail("k", "v")
    rm.mark_degraded("y")
    assert rm.current_stage() is None


def test_context_is_cleared_after_the_stage():
    with rm.stage("s", run_id="r1"):
        assert rm.current_stage() is not None
    assert rm.current_stage() is None


def test_context_is_cleared_after_a_failed_stage():
    with pytest.raises(RuntimeError):
        with rm.stage("s", run_id="r1"):
            raise RuntimeError("boom")
    assert rm.current_stage() is None


def test_nested_stages_restore_the_outer_context():
    with rm.stage("outer", run_id="r1") as outer:
        rm.note_llm_call(1, 1)
        with rm.stage("inner", run_id="r1") as inner:
            rm.note_llm_call(5, 5)
        assert rm.current_stage() is outer
        rm.note_llm_call(1, 1)
    assert inner.prompt_tokens == 5
    assert outer.prompt_tokens == 2, "inner counts must not leak into the outer stage"


def test_run_ids_are_unique():
    assert rm.new_run_id() != rm.new_run_id()


def test_stage_generates_a_run_id_when_absent():
    with rm.stage("s") as rec:
        pass
    assert rec.run_id


# ── Summarisation ──────────────────────────────────────────────────────────

def test_summarize_empty():
    s = rm.summarize([])
    assert s["stages"] == 0 and s["total_duration_sec"] == 0.0
    assert s["peak_vram_bytes"] is None


def test_summarize_totals_and_flags():
    rows = [
        {"stage": "transcription", "duration_sec": 10.0, "model_load_count": 1,
         "llm_call_count": 0, "prompt_tokens": 0, "completion_tokens": 0,
         "retry_count": 0, "outcome": "ok", "peak_vram_bytes": 4000},
        {"stage": "stage2", "duration_sec": 5.5, "model_load_count": 3,
         "llm_call_count": 7, "prompt_tokens": 900, "completion_tokens": 300,
         "retry_count": 2, "outcome": "degraded", "peak_vram_bytes": 9000},
        {"stage": "stage3", "duration_sec": 1.0, "model_load_count": 2,
         "llm_call_count": 1, "prompt_tokens": 100, "completion_tokens": 50,
         "retry_count": 0, "outcome": "failed", "peak_vram_bytes": None},
    ]
    s = rm.summarize(rows)
    assert s["stages"] == 3
    assert s["total_duration_sec"] == 16.5
    assert s["model_load_count"] == 6      # the reload hypothesis, quantified
    assert s["llm_call_count"] == 8
    assert s["prompt_tokens"] == 1000
    assert s["completion_tokens"] == 350
    assert s["retry_count"] == 2
    assert s["failed_stages"] == ["stage3"]
    assert s["degraded_stages"] == ["stage2"]
    assert s["peak_vram_bytes"] == 9000


# ── Persistence against real SQLite ────────────────────────────────────────

@pytest.mark.asyncio
async def test_schema_accepts_a_full_record(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    async with engine.begin() as conn:
        await conn.execute(text(rm.CREATE_TABLE_SQL))
        await conn.execute(text(rm.ADD_INDEX_RUN_SQL))
        await conn.execute(text(rm.ADD_INDEX_RECORDING_SQL))
        await conn.execute(text(rm.ADD_INDEX_STAGE_SQL))

        with rm.stage("stage2", run_id="run-1", recording_id="rec-1", user_id="u-1") as rec:
            rm.note_model_load("qwen3-4b")
            rm.note_llm_call(120, 45)
            rm.note_peak_memory(vram_bytes=8_000_000)
            rm.note_detail("window_count", 12)

        await conn.execute(text(rm.INSERT_SQL), rec.as_row())

        r = await conn.execute(text("SELECT * FROM run_metrics WHERE run_id='run-1'"))
        rows = [dict(x) for x in r.mappings()]

    assert len(rows) == 1
    row = rows[0]
    assert row["stage"] == "stage2"
    assert row["outcome"] == "ok"
    assert row["model_load_count"] == 1
    assert row["prompt_tokens"] == 120
    assert row["peak_vram_bytes"] == 8_000_000
    assert '"window_count": 12' in row["extra"]

    assert rm.summarize(rows)["llm_call_count"] == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_table_creation_is_idempotent(tmp_path):
    """connect_db runs this on every launch."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    for _ in range(3):
        async with engine.begin() as conn:
            await conn.execute(text(rm.CREATE_TABLE_SQL))
            await conn.execute(text(rm.ADD_INDEX_RUN_SQL))
    await engine.dispose()


@pytest.mark.asyncio
async def test_persist_swallows_database_errors(monkeypatch):
    """
    A metrics write must fail quietly. Nothing in this module may be the
    reason a transcription fails.

    The failure is forced rather than assumed absent, because whether a
    database happens to be connected depends on what else has run.
    """
    import database

    def _boom(*a, **k):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(database, "get_db_context", _boom)

    rec = rm.StageRecord(run_id="r", stage="s", started_at="now")
    assert await rm.persist_stage(rec) is False   # returned, not raised


@pytest.mark.asyncio
async def test_fetch_run_swallows_database_errors(monkeypatch):
    import database

    def _boom(*a, **k):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(database, "get_db_context", _boom)
    assert await rm.fetch_run("any-run") == []


@pytest.mark.asyncio
async def test_fetch_run_of_unknown_id_is_empty():
    assert await rm.fetch_run("no-such-run-id") == []


@pytest.mark.asyncio
async def test_async_stage_times_and_isolates_context():
    async with rm.async_stage("transcription", run_id="r1", persist=False) as rec:
        rm.note_model_load("whisper-large-v3")
        await asyncio.sleep(0)
    assert rec.outcome == "ok"
    assert rec.model_load_count == 1
    assert rm.current_stage() is None


@pytest.mark.asyncio
async def test_async_stage_records_failure_and_reraises():
    with pytest.raises(ValueError):
        async with rm.async_stage("stage1", run_id="r1", persist=False) as rec:
            raise ValueError("bad window")
    assert rec.outcome == "failed"
    assert rm.current_stage() is None


@pytest.mark.asyncio
async def test_concurrent_stages_do_not_share_counters():
    """
    Stage 1 windows run concurrently. A context variable keeps each task's
    counters separate; a module-level global would merge them.
    """
    async def work(name, calls):
        async with rm.async_stage(name, run_id="r1", persist=False) as rec:
            for _ in range(calls):
                rm.note_llm_call(10, 5)
                await asyncio.sleep(0)
        return rec

    a, b = await asyncio.gather(work("w1", 3), work("w2", 5))
    assert a.llm_call_count == 3
    assert b.llm_call_count == 5
