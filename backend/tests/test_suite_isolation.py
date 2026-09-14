"""
The test suite must never write into the real installation.

It did: every run created voice profiles "Alice" and "Bob" and duplicate
global-context documents in the user's own database and uploads directory.
conftest.py now redirects the writable runtime paths; these tests keep it so.
"""
from pathlib import Path

import pytest

from config import RUNTIME_DIR, settings

REAL_RUNTIME = Path(RUNTIME_DIR).resolve()


def _is_inside_real_runtime(path_like: str) -> bool:
    raw = str(path_like).replace("sqlite+aiosqlite:///", "")
    try:
        return REAL_RUNTIME in Path(raw).resolve().parents or Path(raw).resolve() == REAL_RUNTIME
    except Exception:
        return False


@pytest.mark.parametrize("name", ["DATABASE_URL", "UPLOAD_DIR", "CHROMADB_DIR", "VECTOR_STORE_DIR"])
def test_writable_paths_are_redirected(name):
    value = getattr(settings, name)
    assert not _is_inside_real_runtime(value), (
        f"{name} points into the real runtime directory during tests: {value}"
    )


def test_database_is_not_the_installation_database():
    assert "runtime/data/voicesum.db" not in settings.DATABASE_URL.replace("\\", "/") or \
        not _is_inside_real_runtime(settings.DATABASE_URL)
