"""Unit tests for the FastAPI ``Depends`` layer.

Uses ``TestClient`` against a throwaway ``FastAPI`` app that mounts the
guards on stub routes. The introspector singleton is replaced with a
fake so no HTTP call is made.
"""

from __future__ import annotations

from typing import Optional

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from gluesync_scheduler.security import corehub_introspect
from gluesync_scheduler.security.auth import (
    current_user,
    require_config,
    require_control,
    require_manage,
)
from gluesync_scheduler.security.corehub_introspect import (
    CoreHubIntrospector,
    CurrentUser,
    set_introspector,
)
from gluesync_scheduler.security.exceptions import IntrospectionError
from gluesync_scheduler.security.user_role import UserRole


# --- Test doubles -----------------------------------------------------


class _FakeIntrospector(CoreHubIntrospector):
    """Bypasses the network entirely; returns whatever the test wants."""

    def __init__(
        self,
        *,
        user: Optional[CurrentUser] = None,
        error: Optional[Exception] = None,
    ) -> None:
        # Skip parent __init__ deliberately; we don't use httpx at all.
        self._user = user
        self._error = error
        self.calls: list[tuple[Optional[str], Optional[str]]] = []

    async def introspect(  # type: ignore[override]
        self,
        *,
        cookie_header: Optional[str],
        authorization_header: Optional[str],
    ) -> Optional[CurrentUser]:
        self.calls.append((cookie_header, authorization_header))
        if self._error is not None:
            raise self._error
        return self._user


# --- Fixtures --------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_introspector():
    """Ensure every test starts with a clean singleton slot."""
    set_introspector(None)
    yield
    set_introspector(None)


@pytest.fixture(autouse=True)
def disable_fail_open(monkeypatch):
    """Guarantee CHRONOS_AUTH_FAIL_OPEN is off for these tests."""
    monkeypatch.delenv("CHRONOS_AUTH_FAIL_OPEN", raising=False)


def _make_app() -> FastAPI:
    """A minimal app with one endpoint per guard, echoing the caller."""
    app = FastAPI()

    @app.get("/any")
    async def any_authenticated(user: CurrentUser = Depends(current_user)):
        return {"user": user.username, "role": user.role.value}

    @app.get("/manage")
    async def manage(user: CurrentUser = Depends(require_manage)):
        return {"user": user.username, "role": user.role.value}

    @app.get("/control")
    async def control(user: CurrentUser = Depends(require_control)):
        return {"user": user.username, "role": user.role.value}

    @app.get("/config")
    async def config(user: CurrentUser = Depends(require_config)):
        return {"user": user.username, "role": user.role.value}

    return app


# --- Unauthenticated --------------------------------------------------


class TestUnauthenticated:
    def test_missing_credentials_returns_401(self) -> None:
        set_introspector(_FakeIntrospector(user=None))
        client = TestClient(_make_app())
        r = client.get("/any")
        assert r.status_code == 401
        assert r.json() == {"detail": "Not authenticated"}

    def test_missing_credentials_hits_all_guards(self) -> None:
        set_introspector(_FakeIntrospector(user=None))
        client = TestClient(_make_app())
        for path in ("/any", "/manage", "/control", "/config"):
            assert client.get(path).status_code == 401

    def test_introspection_error_returns_401(self) -> None:
        set_introspector(
            _FakeIntrospector(error=IntrospectionError("CoreHub down"))
        )
        client = TestClient(_make_app())
        r = client.get("/any")
        assert r.status_code == 401
        assert r.json() == {"detail": "Authentication verification failed"}


# --- Authorized -------------------------------------------------------


ROLE_MATRIX = {
    UserRole.SUPER_ADMIN: {"any": 200, "manage": 200, "control": 200, "config": 200},
    UserRole.MANAGER:     {"any": 200, "manage": 200, "control": 200, "config": 200},
    UserRole.MONITOR:     {"any": 200, "manage": 403, "control": 200, "config": 403},
    UserRole.VIEWER:      {"any": 200, "manage": 403, "control": 403, "config": 403},
    UserRole.EXTERNAL_MODULE: {
        "any": 200, "manage": 200, "control": 200, "config": 403,
    },
}


@pytest.mark.parametrize("role,expected", ROLE_MATRIX.items())
def test_role_matrix(role: UserRole, expected: dict[str, int]) -> None:
    set_introspector(
        _FakeIntrospector(user=CurrentUser(username="alice", role=role))
    )
    client = TestClient(_make_app())
    for path, want in expected.items():
        r = client.get(f"/{path}", headers={"cookie": "gs-auth=x"})
        assert r.status_code == want, f"{role} {path}: got {r.status_code}, want {want}"


class TestForwarding:
    def test_cookie_and_authz_are_forwarded_to_introspector(self) -> None:
        fake = _FakeIntrospector(
            user=CurrentUser(username="a", role=UserRole.SUPER_ADMIN)
        )
        set_introspector(fake)
        client = TestClient(_make_app())

        client.get(
            "/any",
            headers={
                "cookie": "gs-auth=abc",
                "authorization": "Bearer xyz",
            },
        )
        assert fake.calls == [("gs-auth=abc", "Bearer xyz")]


class TestForbiddenDetail:
    def test_403_carries_role_in_message(self) -> None:
        set_introspector(
            _FakeIntrospector(user=CurrentUser(username="a", role=UserRole.VIEWER))
        )
        client = TestClient(_make_app())
        r = client.get("/manage", headers={"cookie": "gs-auth=x"})
        assert r.status_code == 403
        assert "VIEWER" in r.json()["detail"]
        assert "manage schedules" in r.json()["detail"]


# --- Fail-open escape hatch ------------------------------------------


class TestFailOpen:
    def test_fail_open_bypasses_all_guards(self, monkeypatch) -> None:
        monkeypatch.setenv("CHRONOS_AUTH_FAIL_OPEN", "true")
        # No introspector installed at all \u2014 should still succeed.
        set_introspector(None)
        # Also don't set default one; verify by checking response identity.
        client = TestClient(_make_app())

        r = client.get("/manage")
        # Reaches the handler because CHRONOS_AUTH_FAIL_OPEN synthesizes
        # a SUPER_ADMIN user regardless of the missing credentials.
        assert r.status_code == 200
        assert r.json()["role"] == "SUPER_ADMIN"
        assert r.json()["user"] == "__fail_open__"

    @pytest.mark.parametrize("value", ["", "false", "0", "no", "off"])
    def test_fail_open_off_by_default(
        self, monkeypatch, value: str
    ) -> None:
        # A range of "falsy" env values must not turn fail-open on.
        monkeypatch.setenv("CHRONOS_AUTH_FAIL_OPEN", value)
        set_introspector(_FakeIntrospector(user=None))
        client = TestClient(_make_app())
        assert client.get("/manage").status_code == 401
