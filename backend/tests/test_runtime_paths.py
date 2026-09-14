"""
Writable state must not depend on the directory the application is launched from.

Training checkpoints used Path('checkpoints'), and the diagnostics export wrote
voicesum_diagnostics.json, both relative to the process working directory. An
install launched from anywhere else read and wrote a different location, and
the test suite modified files tracked in the repository.
"""
import tempfile
from pathlib import Path

import pytest

from config import settings


@pytest.mark.parametrize("n", [1, 2, 3])
def test_training_checkpoints_follow_the_setting(n):
    import importlib
    module = importlib.import_module(f"services.training.stage{n}_training_service")
    base = getattr(module, f"STAGE{n}_BASE")
    assert base.is_absolute()
    assert base == Path(settings.CHECKPOINTS_DIR) / f"stage{n}"


def test_suite_uses_a_copy_of_the_shipped_checkpoints():
    from config import BASE_DIR
    assert Path(settings.CHECKPOINTS_DIR) != BASE_DIR / "checkpoints"


def test_diagnostics_export_does_not_write_into_the_working_directory(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import dashboard_router
    from routers.auth import require_admin

    app = FastAPI()
    app.include_router(dashboard_router.router)
    app.dependency_overrides[require_admin] = lambda: {"id": "admin"}
    export = next(
        r.path for r in dashboard_router.router.routes
        if "diagnostics" in getattr(r, "path", "") and "export" in r.path
    )

    async def fake_status():
        return {"state": "ok"}

    monkeypatch.setattr(dashboard_router, "get_status_payload", fake_status)
    monkeypatch.chdir(tmp_path)
    with TestClient(app) as client:
        resp = client.get(export)

    assert resp.status_code == 200
    assert "recent_logs" in resp.json()
    assert not any(tmp_path.iterdir()), "export left a file in the working directory"
    leftovers = list(Path(tempfile.gettempdir()).glob("voicesum-diagnostics-*.json"))
    assert not leftovers, "temporary export file was not removed after sending"
