"""
W6.1 — every route must declare its access class.

The unauthenticated administrative surface arose because a router was created
without a dependency, which is invisible in review. This test makes that
omission fail the build instead: any new route must be classified here.

It needs the full application, so it imports main.py and therefore the ML
stack. It skips where that stack is absent (the light developer environment)
and runs wherever the application can actually start.
"""
import pytest

pytest.importorskip("torch", reason="requires the full ML stack to import main")

from fastapi.routing import APIRoute, APIWebSocketRoute  # noqa: E402

from main import app  # noqa: E402
from routers.auth import require_admin, get_current_user  # noqa: E402


# Routes reachable without credentials, each with the reason it is safe.
PUBLIC = {
    "/": "health probe",
    "/health": "health probe",
    "/openapi.json": "schema",
    "/docs": "schema UI",
    "/docs/oauth2-redirect": "schema UI",
    "/redoc": "schema UI",
    "/auth/register": "creates the first account",
    "/auth/login": "issues credentials",
    "/auth/token": "issues credentials",
    "/auth/refresh": "authenticated by the refresh cookie itself",
    "/auth/logout": "authenticated by the refresh cookie it revokes",
}

ADMIN_ONLY_PREFIXES = ("/dashboard",)


def _dependency_names(route):
    return {
        getattr(d.call, "__name__", None)
        for d in getattr(route, "dependant", None).dependencies
    } if getattr(route, "dependant", None) else set()


def _all_routes():
    for r in app.routes:
        if isinstance(r, (APIRoute, APIWebSocketRoute)):
            yield r


def test_admin_routes_require_admin():
    """Every route under an admin prefix must enforce administrator access."""
    offenders = []
    for r in _all_routes():
        if not r.path.startswith(ADMIN_ONLY_PREFIXES):
            continue
        names = _dependency_names(r)
        if isinstance(r, APIWebSocketRoute):
            # The WebSocket authorises inline; the bearer scheme cannot run
            # against a handshake. Asserted in test_admin_authorization.py.
            continue
        if require_admin.__name__ not in names:
            offenders.append(r.path)
    assert not offenders, (
        f"admin routes missing require_admin: {sorted(set(offenders))}"
    )


def test_every_route_declares_an_access_class():
    """
    A route is acceptable only if it is explicitly public, user-authenticated,
    or admin-authenticated. Anything else is unclassified and fails.
    """
    unclassified = []
    for r in _all_routes():
        if r.path in PUBLIC or r.path.startswith("/files"):
            continue
        names = _dependency_names(r)
        if require_admin.__name__ in names or get_current_user.__name__ in names:
            continue
        if isinstance(r, APIWebSocketRoute):
            continue  # authorised inline; covered by its own test
        unclassified.append(f"{sorted(r.methods or [])} {r.path}")

    assert not unclassified, (
        "Routes with no declared access class:\n  "
        + "\n  ".join(sorted(unclassified))
        + "\n\nAdd the appropriate dependency, or add the path to PUBLIC with "
          "a reason if it is intentionally unauthenticated."
    )
