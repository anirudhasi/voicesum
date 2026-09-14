"""
A model-server response must either complete or fail, never hang or truncate.

Observed while running the golden evaluation: generation requests were sent
with no timeout, so when the model server stalled under memory pressure the
whole pipeline blocked indefinitely. Requests now stream, the socket timeout
detects a stall, and a stream cut off before its final chunk is an error rather
than a silently truncated answer.
"""
import io
import json

import pytest

from services.ai_provider import OllamaStreamError, read_ollama_stream


def _stream(*chunks):
    return io.BytesIO(b"".join(json.dumps(c).encode() + b"\n" for c in chunks))


def test_fragments_are_joined_and_final_stats_returned():
    content, final = read_ollama_stream(_stream(
        {"message": {"content": "Hello, "}, "done": False},
        {"message": {"content": "world"}, "done": False},
        {"message": {"content": ""}, "done": True, "eval_count": 7},
    ))
    assert content == "Hello, world"
    assert final["eval_count"] == 7


def test_stream_cut_off_before_done_is_an_error():
    with pytest.raises(OllamaStreamError, match="truncated"):
        read_ollama_stream(_stream({"message": {"content": "partial"}, "done": False}))


def test_error_chunk_is_raised():
    with pytest.raises(OllamaStreamError, match="out of memory"):
        read_ollama_stream(_stream({"error": "out of memory"}))


def test_malformed_chunk_is_raised():
    with pytest.raises(OllamaStreamError, match="malformed"):
        read_ollama_stream(io.BytesIO(b"not json\n"))


def test_blank_lines_are_ignored():
    body = b'\n{"message": {"content": "ok"}, "done": true}\n\n'
    assert read_ollama_stream(io.BytesIO(body))[0] == "ok"


def test_non_streaming_body_is_still_accepted():
    """Objects exposing only read() (single JSON body) are handled."""
    class Body:
        def read(self):
            return json.dumps({"message": {"content": "whole"}, "done": True}).encode()
    assert read_ollama_stream(Body())[0] == "whole"


def test_generation_request_sets_a_timeout_and_streams(monkeypatch):
    import urllib.request
    from services.ai_provider import QwenProvider

    seen = {}

    def fake_urlopen(req, *args, **kwargs):
        if req.full_url.endswith("/api/chat"):
            seen["timeout"] = kwargs.get("timeout")
            seen["stream"] = json.loads(req.data)["stream"]
        return _Resp()

    class _Resp(io.BytesIO):
        status = 200
        def __init__(self):
            super().__init__(b'{"message": {"content": "x"}, "done": true}\n')
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    QwenProvider._call_ollama("http://localhost:11434", "m", "hi", 16)
    assert seen["stream"] is True
    assert seen["timeout"] and seen["timeout"] > 0
