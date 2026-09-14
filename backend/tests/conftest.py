"""
Shared pytest fixtures.

The services in this backend hold module-level singletons for expensive
objects: the language-model provider, the Whisper model, the speaker encoder,
the ChromaDB client. That is correct for production, where loading them once
per process is the whole point, but in a test session every module shares one
process, so a cached instance outlives the test that created it.

The symptom is a test that passes alone and fails in the full suite, which is
the most expensive kind of failure to diagnose. Resetting the caches between
tests removes the class rather than chasing instances of it.
"""
import shutil
import tempfile
from pathlib import Path

import pytest

# Set in pytest_configure; removed in pytest_unconfigure.
_TEST_RUNTIME: Path | None = None


def pytest_configure(config):
    """
    Point every writable runtime path at a throwaway directory before any test
    module is imported.

    Without this the suite wrote into the real installation: running the tests
    created voice profiles named "Alice" and "Bob" and duplicate global-context
    documents in the user's own database and uploads directory, on every run.
    Services read `settings` at call time, so redirecting the shared instance
    here isolates the whole suite. Model directories are left alone: they are
    read-only and the suite needs them.
    """
    global _TEST_RUNTIME
    _TEST_RUNTIME = Path(tempfile.mkdtemp(prefix="voicesum-tests-"))
    (_TEST_RUNTIME / "data").mkdir()

    from config import settings

    settings.DATABASE_URL = (
        f"sqlite+aiosqlite:///{(_TEST_RUNTIME / 'data' / 'voicesum.db').as_posix()}"
    )
    settings.UPLOAD_DIR = str(_TEST_RUNTIME / "uploads")
    settings.CHROMADB_DIR = str(_TEST_RUNTIME / "chromadb")
    settings.VECTOR_STORE_DIR = str(_TEST_RUNTIME / "vector_store")

    # Training checkpoints ship with the repository as defaults, and tests
    # activate and create variants. Work on a copy so the tracked files are
    # never modified by a test run.
    shipped = Path(settings.CHECKPOINTS_DIR)
    checkpoints = _TEST_RUNTIME / "checkpoints"
    if shipped.is_dir():
        shutil.copytree(shipped, checkpoints)
    settings.CHECKPOINTS_DIR = str(checkpoints)


def pytest_unconfigure(config):
    if _TEST_RUNTIME is not None:
        shutil.rmtree(_TEST_RUNTIME, ignore_errors=True)


def isolated_runtime_dir() -> Path:
    """The isolated runtime directory for this test session."""
    assert _TEST_RUNTIME is not None, "pytest_configure has not run"
    return _TEST_RUNTIME


@pytest.fixture(autouse=True)
def reset_service_singletons():
    """
    Clear cached service singletons before and after every test.

    Autouse deliberately: an opt-in fixture only protects the tests that
    remember to ask for it, and the failure mode is order-dependent, so the
    tests that need it are not knowable in advance.

    Each reset is guarded independently. A module that cannot be imported in
    this environment (the ML stack is optional here) must not fail the test
    that happens to run first.
    """
    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def block_unmocked_network(monkeypatch):
    """
    Fail any outbound request a test has not mocked.

    Without this, code paths that call the language model reached whatever
    Ollama server happened to be running on the developer's machine. Tests then
    passed or failed on that model's output, and the full suite took minutes
    longer while it generated. A test that mocks urlopen replaces this guard
    for its own duration, so explicit mocks keep working.
    """
    import urllib.error
    import urllib.request

    def refuse(req, *args, **kwargs):
        url = getattr(req, "full_url", req)
        raise urllib.error.URLError(f"network access blocked in tests: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


def _clear() -> None:
    # Language-model provider: services/ai_provider.py caches a QwenProvider
    # in a module global, so a real or mocked instance persists across tests.
    try:
        import services.ai_provider as ai_provider
        ai_provider._provider_instance = None
    except Exception:
        pass

    # Run metrics: a stage left open by a failing test would attribute the
    # next test's counters to it.
    try:
        from services import run_metrics
        if run_metrics.current_stage() is not None:
            run_metrics._current_stage.set(None)
    except Exception:
        pass

    # Vector store client, keyed by path; a stale path leaks across tests.
    try:
        import services.vector_store as vector_store
        vector_store._chroma_client = None
        vector_store._chroma_client_path = None
    except Exception:
        pass

    # Prompt templates: services/prompt_service.py caches customised templates
    # in a module dict that is only invalidated on write. A test that
    # customises a prompt would otherwise hand that prompt to every later
    # test, changing what the model is asked and so what it returns.
    try:
        import services.prompt_service as prompt_service
        prompt_service._cache.clear()
        prompt_service._cache_loaded = False
    except Exception:
        pass
