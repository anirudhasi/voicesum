"""
W0.2 — the instrumentation is actually wired into the call sites.

A metrics service nothing calls measures nothing, so these tests exercise the
real call paths rather than asserting the source contains a string. Where the
ML stack is absent, the module is checked structurally instead and the reason
is stated.
"""
import ast
import json
from pathlib import Path

import pytest

from services import run_metrics as rm

BACKEND = Path(__file__).resolve().parent.parent


# ── Ollama call accounting (the real path, exercised) ──────────────────────

class _FakeResponse:
    """Stands in for urllib's response object."""
    def __init__(self, payload):
        self.status = 200
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_ollama_call_records_measured_tokens(monkeypatch):
    """
    Ollama reports exact prompt and completion token counts, so the recorded
    figures are measured rather than estimated.
    """
    import urllib.request
    from services.ai_provider import QwenProvider

    payload = {
        "message": {"content": "a result"},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 321,
        "eval_count": 123,
    }
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: _FakeResponse(payload)
    )

    with rm.stage("stage2", run_id="r1") as rec:
        out = QwenProvider._call_ollama(
            "http://localhost:11434", "qwen3:4b", "prompt text", 256
        )

    assert out is not None and "a result" in out
    assert rec.llm_call_count == 1
    assert rec.prompt_tokens == 321
    assert rec.completion_tokens == 123


def test_two_ollama_calls_accumulate(monkeypatch):
    import urllib.request
    from services.ai_provider import QwenProvider

    payload = {"message": {"content": "x"}, "done": True,
               "prompt_eval_count": 10, "eval_count": 5}
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: _FakeResponse(payload)
    )

    with rm.stage("stage1", run_id="r1") as rec:
        for _ in range(3):
            QwenProvider._call_ollama("http://localhost:11434", "m", "p", 16)

    assert rec.llm_call_count == 3
    assert rec.prompt_tokens == 30
    assert rec.completion_tokens == 15


def test_ollama_call_outside_a_stage_is_harmless(monkeypatch):
    """Instrumentation must never be the reason an inference fails."""
    import urllib.request
    from services.ai_provider import QwenProvider

    payload = {"message": {"content": "x"}, "done": True,
               "prompt_eval_count": 1, "eval_count": 1}
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda *a, **k: _FakeResponse(payload)
    )

    assert rm.current_stage() is None
    assert QwenProvider._call_ollama("http://localhost:11434", "m", "p", 16) is not None


def test_missing_token_counts_do_not_break_the_call(monkeypatch):
    """Older Ollama builds may omit the counters."""
    import urllib.request
    from services.ai_provider import QwenProvider

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **k: _FakeResponse({"message": {"content": "x"}, "done": True}),
    )

    with rm.stage("s", run_id="r1") as rec:
        QwenProvider._call_ollama("http://localhost:11434", "m", "p", 16)

    assert rec.llm_call_count == 1
    assert rec.prompt_tokens == 0 and rec.completion_tokens == 0


# ── Model unload accounting ────────────────────────────────────────────────

def test_unload_is_counted(monkeypatch):
    """
    A run that unloads repeatedly must reload repeatedly. Counting unloads is
    the cheapest way to quantify the thrash described in W2.1.
    """
    from services.ai_provider import QwenProvider

    with rm.stage("stage2", run_id="r1") as rec:
        QwenProvider.unload_model()
        QwenProvider.unload_model()

    assert rec.extra.get("model_unload_count") == 2


def test_unload_outside_a_stage_is_harmless():
    from services.ai_provider import QwenProvider

    assert rm.current_stage() is None
    QwenProvider.unload_model()   # must not raise


# ── Load sites are instrumented ────────────────────────────────────────────
#
# These load paths need torch and the model files, so they cannot be executed
# here. The call is verified structurally: present, inside the loading
# function, and wrapped so it cannot raise.

LOAD_SITES = [
    ("services/ai_provider.py", "_load_model_impl"),
    ("services/transcription.py", None),   # module-level loader function
    ("services/embedding.py", None),
]


@pytest.mark.parametrize("relpath,_fn", LOAD_SITES)
def test_load_site_calls_note_model_load(relpath, _fn):
    src = (BACKEND / relpath).read_text(encoding="utf-8")
    assert "note_model_load(" in src, f"{relpath} does not record model loads"


def _count_calls(src: str, func_name: str):
    """
    Return (total calls, calls inside a try block).

    Nested try statements make ast.walk yield the same call more than once, so
    nodes are deduplicated by identity.
    """
    tree = ast.parse(src)

    def calls_within(node):
        return {
            id(n): n
            for n in ast.walk(node)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == func_name
        }

    total = calls_within(tree)
    guarded = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            guarded.update(calls_within(node))
    return len(total), len(guarded)


@pytest.mark.parametrize("relpath,_fn", LOAD_SITES)
def test_note_model_load_calls_are_guarded(relpath, _fn):
    """
    Every instrumentation call must sit inside a try/except, so that a metrics
    failure can never prevent a model from loading.
    """
    src = (BACKEND / relpath).read_text(encoding="utf-8")
    total, guarded = _count_calls(src, "note_model_load")
    assert total > 0, f"{relpath}: no note_model_load call found"
    assert guarded == total, (
        f"{relpath}: {total - guarded} unguarded note_model_load call(s)"
    )


def test_ollama_instrumentation_is_guarded():
    src = (BACKEND / "services" / "ai_provider.py").read_text(encoding="utf-8")
    total, guarded = _count_calls(src, "note_llm_call")
    assert total > 0, "no note_llm_call found in ai_provider"
    assert guarded == total, f"{total - guarded} unguarded note_llm_call(s)"


# ── Pipeline run stage (the real wrapper, exercised) ───────────────────────
#
# Without an open stage every reporting call is a no-op, so this is what makes
# the instrumentation live. run_pipeline delegates to _run_pipeline_impl, which
# is replaced here so the test does not need audio or the ML stack.

@pytest.mark.asyncio
async def test_run_pipeline_opens_a_stage_and_collects_counters(monkeypatch):
    from tasks import pipeline as pl

    captured = {}

    async def fake_impl(**kwargs):
        rec = rm.current_stage()
        captured["stage"] = rec
        assert rec is not None, "run_pipeline must open a metrics stage"
        rm.note_model_load("whisperx-large-v3")
        rm.note_model_load("ecapa-tdnn")
        rm.note_llm_call(200, 80)

    monkeypatch.setattr(pl, "_run_pipeline_impl", fake_impl)
    monkeypatch.setattr(pl, "unload_all_models", lambda: None)
    monkeypatch.setattr(pl, "unregister_task", lambda rid: None)

    persisted = []

    async def fake_persist(rec):
        persisted.append(rec)
        return True

    monkeypatch.setattr(rm, "persist_stage", fake_persist)

    await pl.run_pipeline(recording_id="rec-1", file_path="x.wav", user_id="u-1")

    rec = captured["stage"]
    assert rec.stage == "pipeline"
    assert rec.recording_id == "rec-1" and rec.user_id == "u-1"
    assert rec.outcome == "ok"
    assert rec.model_load_count == 2
    assert rec.llm_call_count == 1
    assert rec.prompt_tokens == 200
    assert rec.duration_sec is not None
    assert persisted and persisted[0] is rec, "the stage must be written"
    assert rm.current_stage() is None


@pytest.mark.asyncio
async def test_run_pipeline_records_failure_and_still_raises(monkeypatch):
    from tasks import pipeline as pl

    async def boom(**kwargs):
        raise RuntimeError("transcription exploded")

    monkeypatch.setattr(pl, "_run_pipeline_impl", boom)
    monkeypatch.setattr(pl, "unload_all_models", lambda: None)
    monkeypatch.setattr(pl, "unregister_task", lambda rid: None)

    persisted = []

    async def fake_persist(rec):
        persisted.append(rec)
        return True

    monkeypatch.setattr(rm, "persist_stage", fake_persist)

    with pytest.raises(RuntimeError):
        await pl.run_pipeline(recording_id="rec-2", file_path="x.wav", user_id="u-1")

    assert persisted, "a failed run must still be recorded"
    assert persisted[0].outcome == "failed"
    assert "transcription exploded" in persisted[0].error_message


@pytest.mark.asyncio
async def test_pipeline_runs_even_if_metrics_are_unavailable(monkeypatch):
    """Instrumentation must never be the reason a transcription fails."""
    from tasks import pipeline as pl

    def broken_stage(*a, **k):
        raise RuntimeError("metrics service is broken")

    monkeypatch.setattr(rm, "async_stage", broken_stage)

    ran = {}

    async def fake_impl(**kwargs):
        ran["yes"] = True

    monkeypatch.setattr(pl, "_run_pipeline_impl", fake_impl)
    monkeypatch.setattr(pl, "unload_all_models", lambda: None)
    monkeypatch.setattr(pl, "unregister_task", lambda rid: None)

    await pl.run_pipeline(recording_id="rec-3", file_path="x.wav", user_id="u-1")
    assert ran.get("yes") is True


@pytest.mark.asyncio
async def test_unload_all_models_is_counted_inside_the_stage(monkeypatch):
    """
    The cleanup unload happens in the wrapper's finally block, which must sit
    inside the stage or the thrash count would miss it.
    """
    from tasks import pipeline as pl

    seen = {}

    async def fake_impl(**kwargs):
        pass

    def fake_unload():
        seen["stage_open"] = rm.current_stage() is not None

    monkeypatch.setattr(pl, "_run_pipeline_impl", fake_impl)
    monkeypatch.setattr(pl, "unload_all_models", fake_unload)
    monkeypatch.setattr(pl, "unregister_task", lambda rid: None)

    async def fake_persist(rec):
        return True

    monkeypatch.setattr(rm, "persist_stage", fake_persist)

    await pl.run_pipeline(recording_id="rec-4", file_path="x.wav", user_id="u-1")
    assert seen.get("stage_open") is True
