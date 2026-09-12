"""
Pooling must follow the model, not a hard-coded assumption.

The service mean-pooled unconditionally. Neither model in this deployment is
trained for that: Qwen3-Embedding declares `pooling_mode_lasttoken`, and
mxbai-embed-large-v1 declares `pooling_mode_cls_token`. Applying the wrong
strategy does not raise. It produces quietly weaker vectors, so retrieval
degrades with nothing in the logs to explain it.

Reading the strategy from the model's own `1_Pooling/config.json` is also what
makes an embedding model swappable, which matters here because the Chinese
origin of Qwen3-Embedding rules it out for this deployment.
"""
import json

import pytest

torch = pytest.importorskip("torch", reason="pooling operates on torch tensors")

from services.text_embedding_service import (  # noqa: E402
    _detect_max_tokens,
    _detect_pooling_mode,
    _pool,
)


def _write_pooling_config(tmp_path, **flags):
    base = {
        "word_embedding_dimension": 1024,
        "pooling_mode_cls_token": False,
        "pooling_mode_mean_tokens": False,
        "pooling_mode_max_tokens": False,
        "pooling_mode_lasttoken": False,
    }
    base.update(flags)
    d = tmp_path / "1_Pooling"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(base), encoding="utf-8")
    return tmp_path


# ── Detection ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("flag,expected", [
    ("pooling_mode_cls_token", "cls"),
    ("pooling_mode_mean_tokens", "mean"),
    ("pooling_mode_lasttoken", "lasttoken"),
    ("pooling_mode_max_tokens", "max"),
])
def test_declared_pooling_is_detected(tmp_path, flag, expected):
    _write_pooling_config(tmp_path, **{flag: True})
    assert _detect_pooling_mode(tmp_path) == expected


def test_missing_config_falls_back_to_mean(tmp_path):
    """Must not crash on a model that ships no pooling config."""
    assert _detect_pooling_mode(tmp_path) == "mean"


def test_config_with_no_flag_set_falls_back_to_mean(tmp_path):
    _write_pooling_config(tmp_path)
    assert _detect_pooling_mode(tmp_path) == "mean"


def test_unreadable_config_falls_back_to_mean(tmp_path):
    d = tmp_path / "1_Pooling"
    d.mkdir(parents=True)
    (d / "config.json").write_text("{ not json", encoding="utf-8")
    assert _detect_pooling_mode(tmp_path) == "mean"


# ── The real models on disk ────────────────────────────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("mxbai-embed-large-v1", "cls"),
    ("Qwen3-Embedding-4B-Instruct", "lasttoken"),
])
def test_installed_models_declare_expected_pooling(name, expected):
    """
    Guards the specific finding: both installed models want something other
    than the mean pooling the service used to apply unconditionally.
    """
    from pathlib import Path
    model_dir = Path(__file__).resolve().parent.parent / "runtime" / "embeddings" / name
    if not model_dir.is_dir():
        pytest.skip(f"{name} not installed in this environment")
    assert _detect_pooling_mode(model_dir) == expected


# ── Pooling maths ──────────────────────────────────────────────────────────

def _batch():
    # One sequence of 3 real tokens, one of 2 real tokens plus padding.
    emb = torch.tensor([
        [[1.0, 0.0], [2.0, 0.0], [3.0, 0.0]],
        [[4.0, 1.0], [6.0, 3.0], [0.0, 0.0]],
    ])
    mask = torch.tensor([[1, 1, 1], [1, 1, 0]])
    return emb, mask


def test_cls_pooling_takes_the_first_token():
    emb, mask = _batch()
    out = _pool(emb, mask, "cls")
    assert torch.allclose(out, torch.tensor([[1.0, 0.0], [4.0, 1.0]]))


def test_mean_pooling_ignores_padding():
    """The second sequence must average over 2 tokens, not 3."""
    emb, mask = _batch()
    out = _pool(emb, mask, "mean")
    assert torch.allclose(out, torch.tensor([[2.0, 0.0], [5.0, 2.0]]))


def test_last_token_pooling_respects_per_sequence_length():
    """
    The last real token is not the last column when sequences differ in
    length. Taking [:, -1] would read padding for the shorter sequence.
    """
    emb, mask = _batch()
    out = _pool(emb, mask, "lasttoken")
    assert torch.allclose(out, torch.tensor([[3.0, 0.0], [6.0, 3.0]]))


def test_max_pooling_ignores_padding():
    emb, mask = _batch()
    out = _pool(emb, mask, "max")
    assert torch.allclose(out, torch.tensor([[3.0, 0.0], [6.0, 3.0]]))


def test_unknown_mode_falls_back_to_mean():
    emb, mask = _batch()
    assert torch.allclose(_pool(emb, mask, "nonsense"), _pool(emb, mask, "mean"))


def test_strategies_differ_on_the_same_input():
    """If these agreed, the choice would not matter and this work would be moot."""
    emb, mask = _batch()
    results = [_pool(emb, mask, m) for m in ("cls", "mean", "lasttoken")]
    assert not torch.allclose(results[0], results[1])
    assert not torch.allclose(results[1], results[2])


def test_output_shape_is_one_vector_per_sequence():
    emb, mask = _batch()
    for mode in ("cls", "mean", "lasttoken", "max"):
        assert _pool(emb, mask, mode).shape == (2, 2), mode


def test_single_token_sequence():
    emb = torch.tensor([[[7.0, 8.0]]])
    mask = torch.tensor([[1]])
    for mode in ("cls", "mean", "lasttoken", "max"):
        assert torch.allclose(_pool(emb, mask, mode), torch.tensor([[7.0, 8.0]])), mode


# ── Token limit detection ──────────────────────────────────────────────────

class _Cfg:
    def __init__(self, n):
        self.max_position_embeddings = n


class _Model:
    def __init__(self, n):
        self.config = _Cfg(n)


class _Tok:
    def __init__(self, n):
        self.model_max_length = n


def test_max_tokens_prefers_the_tokenizer():
    assert _detect_max_tokens(_Model(4096), _Tok(512)) == 512


def test_max_tokens_ignores_the_no_limit_sentinel():
    """Tokenizers use a huge value to mean "unspecified"; it is not a limit."""
    assert _detect_max_tokens(_Model(512), _Tok(int(1e30))) == 512


def test_max_tokens_returns_none_when_undeclared():
    assert _detect_max_tokens(_Model(None), _Tok(None)) is None


def test_chunk_size_fits_the_embedding_window():
    """
    BERT-based embedders cap at 512 tokens and truncate silently beyond it.
    RAG_CHUNK_SIZE is in words; English averages well under 2 tokens a word,
    so this keeps a margin.
    """
    from config import settings
    assert settings.RAG_CHUNK_SIZE * 1.6 < 512, (
        f"RAG_CHUNK_SIZE={settings.RAG_CHUNK_SIZE} words may exceed a 512-token "
        "embedding window, which truncates the tail of every chunk"
    )
