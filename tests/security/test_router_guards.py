"""Integration tests for the real chronos routers with security guards.

These exercise the actual ``gluesync_scheduler.api.router`` and
``settings_router`` modules \u2014 not stub routes \u2014 to catch mis-wired
``Depends(...)`` on real endpoints. The introspector is faked so no
network I/O happens; the ``JobService`` / ``SettingsService`` layer is
mocked so no DB is required.

The pipeline router is deliberately **not** covered here: it is
localhost-only and used by chronos's own cron jobs, not by the browser.
See ``docs/auth-plan.md`` \u00a76 (module-to-module callers).
"""

from __future__ import annotations

import asyncio
import sys
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest


# The chronos ``scheduler_service`` singleton is materialised at module
# import time and requires a running asyncio event loop. Prime it once
# for the whole test session so the real routers can be imported.
def _prime_scheduler_service():
    if "gluesync_scheduler.services.scheduler_service" in sys.modules:
        return

    async def _run():
        import gluesync_scheduler.services.scheduler_service  # noqa: F401

    asyncio.new_event_loop().run_until_complete(_run())


_prime_scheduler_service()


# --- Fixtures & helpers ----------------------------------------------


@pytest.fixture(autouse=True)
def reset_introspector_and_env(monkeypatch):
    """Every test starts with a clean introspector slot and fail-open off."""
    monkeypatch.delenv("CHRONOS_AUTH_FAIL_OPEN", raising=False)
    # Import lazily so we don't trigger scheduler init at test-collection.
    from gluesync_scheduler.security.corehub_introspect import set_introspector

    set_introspector(None)
    yield
    set_introspector(None)


class _FakeIntrospector:
    """Mimics ``CoreHubIntrospector.introspect`` without HTTP.

    Not a subclass \u2014 duck-typed so we can skip the real ``__init__``
    (which would try to reach the SDK client).
    """

    def __init__(self, *, user=None, error=None):
        self._user = user
        self._error = error

    async def introspect(self, *, cookie_header: Optional[str], authorization_header: Optional[str]):
        if self._error:
            raise self._error
        return self._user


def _install_user(role_str: Optional[str], username: str = "alice"):
    """Install a fake introspector on the singleton slot.

    ``role_str`` == ``None`` means "unauthenticated" (introspector returns
    ``None``, the ``Depends`` layer will raise 401).
    """
    from gluesync_scheduler.security.corehub_introspect import (
        CurrentUser,
        set_introspector,
    )
    from gluesync_scheduler.security.user_role import UserRole

    if role_str is None:
        set_introspector(_FakeIntrospector(user=None))
    else:
        set_introspector(
            _FakeIntrospector(
                user=CurrentUser(username=username, role=UserRole(role_str))
            )
        )


def _build_jobs_app():
    """Build a FastAPI app mounting the real jobs router with mocked DB.

    Imported lazily so ``scheduler_service`` initialisation (which needs
    an event loop) happens at test-time, not at collection time.
    """
    from fastapi import FastAPI

    from gluesync_scheduler.api.router import router as jobs_router
    from gluesync_scheduler.db.database import get_db

    app = FastAPI()
    app.include_router(jobs_router, prefix="/api")

    def _fake_db():
        yield MagicMock()

    app.dependency_overrides[get_db] = _fake_db
    return app


def _build_settings_app():
    from fastapi import FastAPI

    from gluesync_scheduler.api.settings_router import router as settings_router
    from gluesync_scheduler.db.database import get_db

    app = FastAPI()
    app.include_router(settings_router, prefix="/api")

    def _fake_db():
        yield MagicMock()

    app.dependency_overrides[get_db] = _fake_db
    return app


@pytest.fixture
def jobs_app():
    return _build_jobs_app()


@pytest.fixture
def settings_app():
    return _build_settings_app()


def _client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)


# --- Test matrix -----------------------------------------------------


# (method, path, [body], required_role) for each JOBS route.
#
# ``required_role`` values map to which UserRole strings return 2xx
# (all others should return 403 when authenticated, 401 when not).
JOBS_ROUTES = [
    # (method, path, body, guard-name)
    ("GET",    "/api/jobs/",           None, "current_user"),
    ("GET",    "/api/jobs/42",         None, "current_user"),
    ("POST",   "/api/jobs/",           {"stub": True}, "require_manage"),
    ("PUT",    "/api/jobs/42",         {"stub": True}, "require_manage"),
    ("POST",   "/api/jobs/42/run",     None, "require_control"),
    ("PATCH",  "/api/jobs/42/status",  {"enabled": True}, "require_control"),
    ("DELETE", "/api/jobs/42",         None, "require_manage"),
]

SETTINGS_ROUTES = [
    ("GET",  "/api/settings/",         None, "current_user"),
    ("GET",  "/api/settings/timezone", None, "current_user"),
    ("PUT",  "/api/settings/timezone", {"value": "UTC"}, "require_config"),
    ("POST", "/api/settings/",         {"key": "k", "value": "v"}, "require_config"),
]


# Role \u2192 what each guard allows (mirrors test_auth_dependencies.py).
GUARD_ALLOWS = {
    "current_user":    {"SUPER_ADMIN", "MANAGER", "MONITOR", "VIEWER", "EXTERNAL_MODULE"},
    "require_control": {"SUPER_ADMIN", "MANAGER", "MONITOR"},
    "require_manage":  {"SUPER_ADMIN", "MANAGER"},
    "require_config":  {"SUPER_ADMIN", "MANAGER"},
}


def _do_request(client, method: str, path: str, body):
    """Issue the request, tolerating post-guard handler crashes.

    We only care about the guard-level status codes (401 / 403 / 200-ish).
    When the guard lets the request through, the mocked DB may cause
    Pydantic ``ValidationError`` inside the handler; that surfaces from
    ``TestClient`` as a raised exception rather than an HTTP response.
    Treat any such post-guard crash as ``500`` — same status class as
    a real handler bug — so the assertion "not 401 and not 403"
    remains meaningful.
    """
    try:
        if method == "GET":
            return client.get(path)
        if method == "DELETE":
            return client.delete(path)
        if method == "POST":
            return client.post(path, json=body)
        if method == "PUT":
            return client.put(path, json=body)
        if method == "PATCH":
            return client.patch(path, json=body)
        raise AssertionError(f"unhandled method {method}")
    except Exception as exc:
        # Return an object that quacks like a response but signals
        # "guard passed, handler exploded" — which is fine for our
        # security-level assertions.
        class _PostGuardCrash:
            status_code = 500
            _exc = exc

            def json(self):
                return {"detail": f"post-guard handler crash: {type(self._exc).__name__}"}

        return _PostGuardCrash()


# --- Unauthenticated ------------------------------------------------


class TestUnauthenticated:
    """No credentials \u2192 401 on every guarded route (including reads)."""

    @pytest.mark.parametrize("method,path,body,_guard", JOBS_ROUTES)
    def test_jobs_401(self, jobs_app, method, path, body, _guard):
        _install_user(None)
        with _client(jobs_app) as client:
            r = _do_request(client, method, path, body)
        assert r.status_code == 401, (
            f"{method} {path} \u2192 {r.status_code}, want 401"
        )

    @pytest.mark.parametrize("method,path,body,_guard", SETTINGS_ROUTES)
    def test_settings_401(self, settings_app, method, path, body, _guard):
        _install_user(None)
        with _client(settings_app) as client:
            r = _do_request(client, method, path, body)
        assert r.status_code == 401


# --- Forbidden ---------------------------------------------------------


ALL_ROLES = ["SUPER_ADMIN", "MANAGER", "MONITOR", "VIEWER", "EXTERNAL_MODULE"]


class TestRoleMatrix:
    """Every (role, endpoint) combination \u2192 allowed vs 403.

    We only care about the status classes:
      - allowed  \u2192 must NOT be 401/403 (the real handler may still 404
        or 500 because the DB is a MagicMock, but the guard passed).
      - denied   \u2192 must be exactly 403.
    """

    @pytest.mark.parametrize("role", ALL_ROLES)
    @pytest.mark.parametrize("method,path,body,guard", JOBS_ROUTES)
    def test_jobs(self, jobs_app, role, method, path, body, guard):
        _install_user(role)
        allowed = role in GUARD_ALLOWS[guard]

        with _client(jobs_app) as client:
            r = _do_request(client, method, path, body)

        if allowed:
            assert r.status_code not in (401, 403), (
                f"{role} {method} {path} \u2192 {r.status_code}, expected pass through guard"
            )
        else:
            assert r.status_code == 403, (
                f"{role} {method} {path} \u2192 {r.status_code}, want 403"
            )

    @pytest.mark.parametrize("role", ALL_ROLES)
    @pytest.mark.parametrize("method,path,body,guard", SETTINGS_ROUTES)
    def test_settings(self, settings_app, role, method, path, body, guard):
        _install_user(role)
        allowed = role in GUARD_ALLOWS[guard]

        with _client(settings_app) as client:
            r = _do_request(client, method, path, body)

        if allowed:
            assert r.status_code not in (401, 403), (
                f"{role} {method} {path} \u2192 {r.status_code}, expected pass through guard"
            )
        else:
            assert r.status_code == 403


# --- Fail-open sanity ------------------------------------------------


class TestFailOpen:
    """When ``CHRONOS_AUTH_FAIL_OPEN=true``, every guard passes through."""

    def test_manage_route_passes_without_auth(self, monkeypatch, jobs_app):
        monkeypatch.setenv("CHRONOS_AUTH_FAIL_OPEN", "true")
        # No introspector installed, no credentials sent.
        with _client(jobs_app) as client:
            r = _do_request(client, "DELETE", "/api/jobs/42", None)
        # The guard passes; the actual handler may 500 because the DB is
        # mocked (via ``_do_request`` crash tolerance), but we must NOT
        # see 401 or 403.
        assert r.status_code not in (401, 403), (
            f"fail-open should have bypassed the guard, got {r.status_code}"
        )
