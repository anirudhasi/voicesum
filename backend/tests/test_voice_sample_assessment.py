"""
Voice enrolment must say what went wrong, and never blame the user for an
internal fault (W4.3, scoped to enrolment).

Observed on a live install: a missing audio-decoding library made every sample
fail, and the application answered "Could not extract embeddings from samples.
Please re-record." Re-recording could never succeed. Every failure had been
reduced to None, so the router could not tell a silent microphone from a broken
server.
"""
import numpy as np
import pytest

sf = pytest.importorskip("soundfile")

from services import embedding as emb  # noqa: E402
from services.embedding import (  # noqa: E402
    MIN_SAMPLE_SEC,
    SampleOutcome,
    assess_voice_sample,
    summarise_sample_failures,
)

SR = 16000


def _wav(tmp_path, seconds, amplitude=0.2, name="s.wav"):
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    signal = (amplitude * np.sin(2 * np.pi * 180 * t)).astype(np.float32)
    path = tmp_path / name
    sf.write(str(path), signal, SR)
    return path


# ── User-correctable ───────────────────────────────────────────────────────

def test_too_short_sample_is_user_correctable(tmp_path):
    out = assess_voice_sample(str(_wav(tmp_path, 0.8)))
    assert out.problem == "too_short" and out.user_correctable
    assert "0.8 s" in out.message and "at least" in out.message


def test_too_quiet_sample_is_user_correctable(tmp_path):
    out = assess_voice_sample(str(_wav(tmp_path, 3.0, amplitude=0.0001)))
    assert out.problem == "too_quiet" and out.user_correctable
    assert "microphone" in out.message


def test_empty_upload_is_user_correctable(tmp_path):
    path = tmp_path / "empty.wav"
    path.write_bytes(b"")
    out = assess_voice_sample(str(path))
    assert out.problem == "empty" and out.user_correctable


# ── Internal: re-recording cannot help ─────────────────────────────────────

def test_missing_file_is_internal(tmp_path):
    out = assess_voice_sample(str(tmp_path / "nope.wav"))
    assert out.problem == "missing" and not out.user_correctable


def test_undecodable_audio_is_internal_not_a_request_to_re_record(tmp_path, monkeypatch):
    """The live failure: the server could not decode a valid recording."""
    path = _wav(tmp_path, 3.0)

    def broken_decoder(*a, **k):
        raise OSError("Could not load this library: libtorchcodec_core7.dll")

    monkeypatch.setattr(emb, "_load_audio", broken_decoder)
    out = assess_voice_sample(str(path))
    assert out.problem == "decode_failed"
    assert not out.user_correctable
    assert "re-recording will not help" in out.message
    assert "libtorchcodec" not in out.message, "internal detail must not reach the user"


def test_unavailable_model_is_internal(tmp_path, monkeypatch):
    def no_model():
        raise RuntimeError("ECAPA-TDNN model directory not found")

    monkeypatch.setattr(emb, "get_encoder", no_model)
    out = assess_voice_sample(str(_wav(tmp_path, 3.0)))
    assert out.problem == "model_unavailable" and not out.user_correctable
    assert "administrator" in out.message


def test_failed_extraction_is_internal(tmp_path, monkeypatch):
    monkeypatch.setattr(emb, "get_encoder", lambda: object())
    monkeypatch.setattr(emb, "extract_embedding", lambda audio, sr=SR: None)
    out = assess_voice_sample(str(_wav(tmp_path, 3.0)))
    assert out.problem == "embedding_failed" and not out.user_correctable


def test_success(tmp_path, monkeypatch):
    vec = np.ones(192, dtype=np.float32) / np.sqrt(192)
    monkeypatch.setattr(emb, "get_encoder", lambda: object())
    monkeypatch.setattr(emb, "extract_embedding", lambda audio, sr=SR: vec)
    out = assess_voice_sample(str(_wav(tmp_path, 3.0)))
    assert out.ok and out.problem is None
    assert out.duration_sec == pytest.approx(3.0, abs=0.01)


def test_existing_function_keeps_its_contract(tmp_path, monkeypatch):
    """extract_embedding_from_file still returns an array or None for other callers."""
    assert emb.extract_embedding_from_file(str(tmp_path / "missing.wav")) is None
    assert emb.extract_embedding_from_file(str(_wav(tmp_path, 0.5))) is None


# ── Choosing the response ──────────────────────────────────────────────────

def _o(problem, correctable):
    return SampleOutcome(None, problem, correctable, f"msg:{problem}")


def test_internal_failure_takes_precedence():
    """If any sample failed internally, asking to re-record is a dead end."""
    status, detail = summarise_sample_failures([_o("too_quiet", True), _o("decode_failed", False)])
    assert status == 500 and detail == "msg:decode_failed"


def test_all_user_correctable_gives_422_with_specific_guidance():
    status, detail = summarise_sample_failures([_o("too_short", True), _o("too_quiet", True)])
    assert status == 422 and detail == "msg:too_short"


def test_generic_re_record_message_is_gone_from_the_router():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "routers" / "voice.py").read_text(encoding="utf-8")
    assert "Please re-record." not in src
    assert "Please re-record with clearer audio" not in src
    assert src.count("summarise_sample_failures(outcomes)") == 2
