"""
Tests for W6.1 — administrator identity and administrative route lockdown.

Covers the finding that the dashboard router (HTML console, application logs,
diagnostics export, log download and the maintenance endpoint) was reachable
without authentication. Logs carry meeting participant names and speaking
times, and the maintenance endpoint changes runtime state.

require_admin accepts either a bearer token or the HttpOnly refresh cookie.
The cookie path exists for the server-rendered console, whose scripts issue
plain same-origin fetches with no Authorization header.

These tests build a minimal app containing only the dashboard router rather
than importing main.py, so they run without the ML stack.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.auth as auth
from routers.auth import require_admin, is_admin
import routers.dashboard_router as dashboard_router
import routers.analytics_router as analytics_router


ADMIN = {"id": "u-admin", "name": "Admin", "email": "admin@example.com", "role": "admin"}
MEMBER = {"id": "u-member", "name": "Member", "email": "member@example.com", "role": "user"}
LEGACY = {"id": "u-legacy", "name": "Legacy", "email": "legacy@example.com"}  # pre-role row

COOKIE = "vs_refresh"


def _build_app(with_ws: bool = False):
    app = FastAPI()
    app.include_router(dashboard_router.router)
    if with_ws:
        app.include_router(dashboard_router.ws_router)
    return app


def _set_cookie_user(monkeypatch, user):
    """Make the refresh-cookie path resolve to `user` (None for no session)."""
    async def _resolve(raw):
        return user if raw else None
    monkeypatch.setattr(auth, "_user_from_refresh_cookie", _resolve)


def _set_bearer_user(monkeypatch, user):
    """Make the bearer path resolve to `user`, bypassing JWT decoding."""
    monkeypatch.setattr(
        auth.jwt, "decode", lambda *a, **k: {"sub": "u", "sid": "s"}
    )
    async def _resolve(session_id, user_id):
        return user
    monkeypatch.setattr(auth, "_user_for_session", _resolve)


# ── is_admin ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("user,expected", [
    (ADMIN, True),
    (MEMBER, False),
    (LEGACY, False),            # row predating the role column
    ({"id": "u", "role": None}, False),
    ({"id": "u", "role": "Admin"}, False),   # case-sensitive by design
    (None, False),
])
def test_is_admin(user, expected):
    assert is_admin(user) is expected


# ── Route-level enforcement ────────────────────────────────────────────────

# The endpoints named in the observation record as unauthenticated.
PROTECTED_GET = [
    "/dashboard",
    "/dashboard/api/status",
    "/dashboard/api/logs",
    "/dashboard/api/endpoints",
    "/dashboard/api/diagnostics/export",
    "/dashboard/api/diagnostics/logs/download",
]


@pytest.mark.parametrize("path", PROTECTED_GET)
def test_anonymous_is_denied(path, monkeypatch):
    _set_cookie_user(monkeypatch, None)
    resp = TestClient(_build_app()).get(path)
    assert resp.status_code == 401, f"{path} returned {resp.status_code} to an anonymous caller"


@pytest.mark.parametrize("path", PROTECTED_GET)
def test_regular_user_is_denied(path, monkeypatch):
    _set_cookie_user(monkeypatch, MEMBER)
    client = TestClient(_build_app())
    client.cookies.set(COOKIE, "a-members-session")
    assert client.get(path).status_code == 403


@pytest.mark.parametrize("path", PROTECTED_GET)
def test_user_predating_role_column_is_denied(path, monkeypatch):
    """An upgraded install must not hand admin rights to every existing user."""
    _set_cookie_user(monkeypatch, LEGACY)
    client = TestClient(_build_app())
    client.cookies.set(COOKIE, "a-legacy-session")
    assert client.get(path).status_code == 403


def test_maintenance_denied_to_anonymous(monkeypatch):
    _set_cookie_user(monkeypatch, None)
    resp = TestClient(_build_app()).post(
        "/dashboard/api/maintenance", json={"action": "unload_models"}
    )
    assert resp.status_code == 401


def test_maintenance_denied_to_regular_user(monkeypatch):
    _set_cookie_user(monkeypatch, MEMBER)
    client = TestClient(_build_app())
    client.cookies.set(COOKIE, "a-members-session")
    resp = client.post("/dashboard/api/maintenance", json={"action": "unload_models"})
    assert resp.status_code == 403


# ── Admin access via each supported credential ─────────────────────────────

def _reached_handler(resp):
    """Anything other than 401/403 means authorisation let the request through."""
    return resp.status_code not in (401, 403)


def test_admin_allowed_via_refresh_cookie(monkeypatch):
    """The console path: same-origin fetch carrying only the HttpOnly cookie."""
    _set_cookie_user(monkeypatch, ADMIN)
    app = _build_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        client.cookies.set(COOKIE, "an-admin-session")
        resp = client.get("/dashboard/api/status")
    assert _reached_handler(resp)


def test_admin_allowed_via_bearer_token(monkeypatch):
    """The API path: Authorization header, no cookie."""
    _set_bearer_user(monkeypatch, ADMIN)
    _set_cookie_user(monkeypatch, None)
    app = _build_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get(
            "/dashboard/api/status", headers={"Authorization": "Bearer any-token"}
        )
    assert _reached_handler(resp)


def test_non_admin_bearer_is_denied(monkeypatch):
    _set_bearer_user(monkeypatch, MEMBER)
    _set_cookie_user(monkeypatch, None)
    client = TestClient(_build_app())
    resp = client.get(
        "/dashboard/api/status", headers={"Authorization": "Bearer any-token"}
    )
    assert resp.status_code == 403


# ── Cross-site protection on the cookie path ───────────────────────────────

def test_cookie_auth_refused_cross_site(monkeypatch):
    """
    A cross-site request must not be able to drive admin routes on the strength
    of an ambient cookie, even though SameSite=lax would already block POST.
    """
    _set_cookie_user(monkeypatch, ADMIN)
    client = TestClient(_build_app())
    client.cookies.set(COOKIE, "an-admin-session")
    resp = client.get(
        "/dashboard/api/diagnostics/logs/download",
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert resp.status_code == 401


@pytest.mark.parametrize("site", ["same-origin", "same-site", "none"])
def test_cookie_auth_allowed_for_non_cross_site(site, monkeypatch):
    _set_cookie_user(monkeypatch, ADMIN)
    app = _build_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        client.cookies.set(COOKIE, "an-admin-session")
        resp = client.get("/dashboard/api/status", headers={"Sec-Fetch-Site": site})
    assert _reached_handler(resp)


def test_bearer_token_still_works_cross_site(monkeypatch):
    """The cross-site guard applies to the cookie path only, not to bearer."""
    _set_bearer_user(monkeypatch, ADMIN)
    app = _build_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get(
            "/dashboard/api/status",
            headers={"Authorization": "Bearer any-token", "Sec-Fetch-Site": "cross-site"},
        )
    assert _reached_handler(resp)


# ── WebSocket log stream ───────────────────────────────────────────────────
#
# The stream carries application logs, which include participant names. It must
# authorise before accepting. A browser cannot set an Authorization header on a
# handshake, so it authenticates via the HttpOnly refresh cookie.

def test_websocket_is_on_an_unguarded_router():
    """
    The bearer scheme cannot run against a handshake, so the WebSocket must not
    sit behind the router-level dependency. Regression guard: putting it back
    on the main router makes it fail at connect time rather than deny cleanly.
    """
    assert not getattr(dashboard_router.ws_router, "dependencies", []), (
        "ws_router must carry no router-level dependency"
    )
    ws_paths = {
        r.path for r in dashboard_router.router.routes
        if not getattr(r, "methods", None)
    }
    assert not ws_paths, f"WebSocket routes must not be on the guarded router: {ws_paths}"


def test_websocket_denied_without_cookie(monkeypatch):
    async def _no_admin(raw):
        return None
    monkeypatch.setattr(dashboard_router, "resolve_admin_from_refresh_cookie", _no_admin)

    client = TestClient(_build_app(with_ws=True))
    with pytest.raises(Exception) as exc:
        with client.websocket_connect("/dashboard/ws"):
            pass
    # Closed with a policy code, not crashed while resolving the dependency.
    assert "TypeError" not in type(exc.value).__name__


def test_websocket_denied_for_non_admin(monkeypatch):
    async def _no_admin(raw):
        return None  # the resolver returns None for non-admins by contract
    monkeypatch.setattr(dashboard_router, "resolve_admin_from_refresh_cookie", _no_admin)

    client = TestClient(_build_app(with_ws=True))
    client.cookies.set(COOKIE, "a-non-admin-session")
    with pytest.raises(Exception):
        with client.websocket_connect("/dashboard/ws"):
            pass


def test_websocket_accepts_admin(monkeypatch):
    async def _admin(raw):
        return ADMIN
    monkeypatch.setattr(dashboard_router, "resolve_admin_from_refresh_cookie", _admin)

    client = TestClient(_build_app(with_ws=True))
    client.cookies.set(COOKIE, "an-admin-session")
    try:
        with client.websocket_connect("/dashboard/ws") as ws:
            assert ws.receive_json()["type"] == "initial_logs"
    except Exception as e:
        # The handler imports main (ML stack) after accepting; reaching that
        # point still proves authorisation passed.
        assert "ModuleNotFoundError" in repr(e) or "torch" in repr(e), repr(e)


def test_resolve_admin_from_refresh_cookie_rejects_non_admin(monkeypatch):
    async def _member(raw):
        return MEMBER
    monkeypatch.setattr(auth, "_user_from_refresh_cookie", _member)
    import asyncio
    assert asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        auth.resolve_admin_from_refresh_cookie("x")
    ) is None


# ── Analytics router ───────────────────────────────────────────────────────
#
# GET /analysis was unauthenticated and returns up to 1000 processing records
# spanning every user, carrying user ids, recording ids and pipeline error
# messages. Reclassified as administrator-only.

def _analytics_app():
    app = FastAPI()
    app.include_router(analytics_router.router)
    return app


def test_analytics_denied_to_anonymous(monkeypatch):
    _set_cookie_user(monkeypatch, None)
    assert TestClient(_analytics_app()).get("/analysis").status_code == 401


def test_analytics_denied_to_regular_user(monkeypatch):
    _set_cookie_user(monkeypatch, MEMBER)
    client = TestClient(_analytics_app())
    client.cookies.set(COOKIE, "a-members-session")
    assert client.get("/analysis").status_code == 403


def test_analytics_summary_denied_to_regular_user(monkeypatch):
    """The aggregate view is still cross-user data."""
    _set_cookie_user(monkeypatch, MEMBER)
    client = TestClient(_analytics_app())
    client.cookies.set(COOKIE, "a-members-session")
    assert client.get("/analysis?summary=true").status_code == 403


def test_analytics_allowed_for_admin(monkeypatch):
    _set_cookie_user(monkeypatch, ADMIN)
    app = _analytics_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        client.cookies.set(COOKIE, "an-admin-session")
        resp = client.get("/analysis")
    assert _reached_handler(resp)


def test_analytics_router_declares_admin_dependency():
    deps = getattr(analytics_router.router, "dependencies", [])
    assert any(getattr(d, "dependency", None) is require_admin for d in deps)


# ── Router wiring ──────────────────────────────────────────────────────────

def test_router_declares_admin_dependency():
    """Guards against the dependency being dropped during a future refactor."""
    deps = getattr(dashboard_router.router, "dependencies", [])
    assert any(
        getattr(d, "dependency", None) is require_admin for d in deps
    ), "dashboard router must carry a router-level require_admin dependency"


def test_every_dashboard_route_is_covered():
    """
    Every HTTP route on the router must be asserted by this file. Fails if a
    route is added without declaring its access class here.
    """
    http_paths = {
        r.path for r in dashboard_router.router.routes
        if getattr(r, "methods", None)
    }
    uncovered = http_paths - set(PROTECTED_GET) - {"/dashboard/api/maintenance"}
    assert not uncovered, (
        f"dashboard routes not asserted in this test: {sorted(uncovered)}. "
        "Add them to PROTECTED_GET or assert their access class explicitly."
    )


def test_router_level_dependency_can_be_overridden_in_tests():
    """
    tests/test_dashboard_router.py authenticates by overriding require_admin
    through app.dependency_overrides. Router-level dependencies resolve
    differently from route-level ones, so the mechanism is pinned here: if a
    future FastAPI changes it, that suite would otherwise fail with a
    confusing 401 rather than pointing at the cause.
    """
    app = _build_app()
    app.dependency_overrides[require_admin] = lambda: ADMIN
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/dashboard/api/status")
    assert resp.status_code not in (401, 403), (
        "dependency_overrides no longer applies to router-level dependencies"
    )
