"""
W4.1 — the shipped placeholder JWT secret must never sign a token.

The application ships as a built artefact. A signing key baked into the build
would be readable by anyone holding a copy and would mint valid tokens for
every installation, so each install generates and persists its own.
"""
import importlib

import pytest

import config as config_module
from config import (
    MIN_JWT_SECRET_LENGTH,
    PLACEHOLDER_JWT_SECRET,
    Settings,
    _resolve_jwt_secret,
)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    """Point secret storage at a temporary runtime directory."""
    monkeypatch.setattr(config_module, "RUNTIME_DIR", tmp_path)
    return tmp_path


# ── Placeholder must never survive ─────────────────────────────────────────

def test_placeholder_is_replaced(runtime):
    resolved = _resolve_jwt_secret(PLACEHOLDER_JWT_SECRET)
    assert resolved != PLACEHOLDER_JWT_SECRET
    assert len(resolved) >= MIN_JWT_SECRET_LENGTH


def test_empty_secret_is_replaced(runtime):
    assert _resolve_jwt_secret("") != PLACEHOLDER_JWT_SECRET


def test_whitespace_only_secret_is_replaced(runtime):
    assert _resolve_jwt_secret("   ") != PLACEHOLDER_JWT_SECRET


def test_settings_never_expose_the_placeholder(runtime):
    """The wiring, not just the helper: a default-constructed Settings is safe."""
    s = Settings(_env_file=None)
    assert s.JWT_SECRET != PLACEHOLDER_JWT_SECRET
    assert len(s.JWT_SECRET) >= MIN_JWT_SECRET_LENGTH


# ── Persistence across restarts ────────────────────────────────────────────

def test_secret_is_written_to_the_runtime_directory(runtime):
    _resolve_jwt_secret(PLACEHOLDER_JWT_SECRET)
    key = runtime / "secrets" / "jwt_secret.key"
    assert key.is_file(), "the generated key must persist"
    assert len(key.read_text(encoding="utf-8").strip()) >= MIN_JWT_SECRET_LENGTH


def test_secret_is_stable_across_calls(runtime):
    """Regenerating on every start would invalidate every session on restart."""
    first = _resolve_jwt_secret(PLACEHOLDER_JWT_SECRET)
    second = _resolve_jwt_secret(PLACEHOLDER_JWT_SECRET)
    assert first == second


def test_each_installation_gets_a_distinct_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "RUNTIME_DIR", tmp_path / "install_a")
    a = _resolve_jwt_secret(PLACEHOLDER_JWT_SECRET)
    monkeypatch.setattr(config_module, "RUNTIME_DIR", tmp_path / "install_b")
    b = _resolve_jwt_secret(PLACEHOLDER_JWT_SECRET)
    assert a != b, "a shared secret across installs is equivalent to no secret"


def test_truncated_stored_key_is_regenerated(runtime):
    """A short or corrupted key file must not be trusted."""
    key = runtime / "secrets" / "jwt_secret.key"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text("too-short", encoding="utf-8")
    resolved = _resolve_jwt_secret(PLACEHOLDER_JWT_SECRET)
    assert len(resolved) >= MIN_JWT_SECRET_LENGTH
    assert resolved != "too-short"


# ── Explicit override ──────────────────────────────────────────────────────

def test_explicit_secret_is_honoured(runtime):
    explicit = "x" * 64
    assert _resolve_jwt_secret(explicit) == explicit


def test_explicit_secret_takes_precedence_over_stored(runtime):
    stored = _resolve_jwt_secret(PLACEHOLDER_JWT_SECRET)
    explicit = "y" * 64
    assert _resolve_jwt_secret(explicit) == explicit != stored


def test_short_explicit_secret_is_refused(runtime):
    """Failing loudly beats silently falling back to a generated key."""
    with pytest.raises(RuntimeError) as exc:
        _resolve_jwt_secret("short")
    assert "at least" in str(exc.value)


@pytest.mark.parametrize("length", [MIN_JWT_SECRET_LENGTH - 1, MIN_JWT_SECRET_LENGTH])
def test_length_boundary(runtime, length):
    candidate = "z" * length
    if length < MIN_JWT_SECRET_LENGTH:
        with pytest.raises(RuntimeError):
            _resolve_jwt_secret(candidate)
    else:
        assert _resolve_jwt_secret(candidate) == candidate


def test_explicit_secret_is_stripped(runtime):
    padded = "  " + ("q" * 40) + "  "
    assert _resolve_jwt_secret(padded) == "q" * 40
