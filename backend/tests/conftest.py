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
import pytest


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
