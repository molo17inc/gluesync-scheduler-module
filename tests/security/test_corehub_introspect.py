"""Unit tests for the CoreHub introspector.

Uses ``httpx.MockTransport`` to intercept outbound requests without a
real server. Cache behaviour, failure modes, and header forwarding are
all covered here.
"""

from __future__ import annotations

from typing import Callable

import httpx
import pytest

from gluesync_scheduler.security.corehub_introspect import (
    CoreHubIntrospector,
    CurrentUser,
)
from gluesync_scheduler.security.exceptions import IntrospectionError
from gluesync_scheduler.security.user_role import UserRole


COREHUB_URL = "https://corehub.test:1717"
VALID_ME_BODY = {
    "id": 1,
    "username": "daniele",
    "role": "MANAGER",
    "name": "Daniele",
    "surname": "Angeli",
    "email": "daniele@example.com",
    "isOidcUser": False,
    "changeRequired": False,
}


@pytest.fixture
def mock_transport_factory(monkeypatch):
    """Return a helper that installs a MockTransport for the test."""

    def _install(handler: Callable[[httpx.Request], httpx.Response]):
        transport = httpx.MockTransport(handler)

        real_init = httpx.AsyncClient.__init__

        def _patched_init(self, *args, **kwargs):
            kwargs["transport"] = transport
            real_init(self, *args, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", _patched_init)
        return transport

    return _install


def _introspector(cache_ttl: int = 30, corehub_url=COREHUB_URL) -> CoreHubIntrospector:
    return CoreHubIntrospector(
        corehub_url_provider=lambda: corehub_url,
        cache_ttl_seconds=cache_ttl,
        timeout_seconds=1.0,
        verify_ssl=False,
    )


# --- Cache-key derivation --------------------------------------------


class TestCacheKey:
    def test_no_credentials_yields_no_key(self) -> None:
        ins = _introspector()
        assert (
            ins._cache_key(cookie_header=None, authorization_header=None)
            is None
        )

    def test_empty_credentials_yield_no_key(self) -> None:
        ins = _introspector()
        assert ins._cache_key(cookie_header="", authorization_header="") is None

    def test_authorization_header_wins_over_cookie(self) -> None:
        ins = _introspector()
        key = ins._cache_key(
            cookie_header="gs-auth=cookie-tok",
            authorization_header="Bearer bearer-tok",
        )
        assert key == "authz:Bearer bearer-tok"

    def test_gs_auth_cookie_extracted_from_multi_cookie_header(self) -> None:
        ins = _introspector()
        key = ins._cache_key(
            cookie_header="foo=1; gs-auth=my-token; bar=2",
            authorization_header=None,
        )
        assert key == "cookie:my-token"

    def test_missing_gs_auth_cookie_yields_no_key(self) -> None:
        ins = _introspector()
        assert (
            ins._cache_key(
                cookie_header="foo=1; bar=2",
                authorization_header=None,
            )
            is None
        )

    def test_different_tokens_have_different_keys(self) -> None:
        # Two sessions for the same user must NOT collide in the cache;
        # one could be revoked and the other still live.
        ins = _introspector()
        k1 = ins._cache_key(
            cookie_header="gs-auth=aaa", authorization_header=None
        )
        k2 = ins._cache_key(
            cookie_header="gs-auth=bbb", authorization_header=None
        )
        assert k1 != k2


# --- Happy path -------------------------------------------------------


@pytest.mark.asyncio
class TestIntrospectHappyPath:
    async def test_returns_current_user_on_200(
        self, mock_transport_factory
    ) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=VALID_ME_BODY)

        mock_transport_factory(handler)
        ins = _introspector()

        user = await ins.introspect(
            cookie_header="gs-auth=tok", authorization_header=None
        )

        assert user == CurrentUser(username="daniele", role=UserRole.MANAGER)
        assert len(seen) == 1
        assert seen[0].url.path == "/auth/me"
        assert seen[0].headers.get("Cookie") == "gs-auth=tok"

    async def test_forwards_authorization_header(
        self, mock_transport_factory
    ) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=VALID_ME_BODY)

        mock_transport_factory(handler)
        ins = _introspector()

        user = await ins.introspect(
            cookie_header=None, authorization_header="Bearer xyz"
        )

        assert user is not None
        assert seen[0].headers.get("Authorization") == "Bearer xyz"

    @pytest.mark.parametrize(
        "role",
        [
            UserRole.SUPER_ADMIN,
            UserRole.MANAGER,
            UserRole.MONITOR,
            UserRole.VIEWER,
            UserRole.EXTERNAL_MODULE,
        ],
    )
    async def test_maps_all_roles(
        self, role: UserRole, mock_transport_factory
    ) -> None:
        # Parametrise instead of looping so each role gets a fresh
        # ``mock_transport_factory`` install (monkeypatch of
        # ``httpx.AsyncClient.__init__`` doesn't stack cleanly across
        # iterations inside a single test).
        body = {**VALID_ME_BODY, "role": role.value, "username": "u"}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=body)

        mock_transport_factory(handler)
        ins = _introspector()
        user = await ins.introspect(
            cookie_header="gs-auth=t", authorization_header=None
        )
        assert user is not None
        assert user.role == role


# --- Cache behaviour --------------------------------------------------


@pytest.mark.asyncio
class TestCacheBehaviour:
    async def test_second_call_is_cache_hit(
        self, mock_transport_factory
    ) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=VALID_ME_BODY)

        mock_transport_factory(handler)
        ins = _introspector(cache_ttl=30)

        u1 = await ins.introspect(
            cookie_header="gs-auth=tok", authorization_header=None
        )
        u2 = await ins.introspect(
            cookie_header="gs-auth=tok", authorization_header=None
        )

        assert u1 == u2
        assert call_count == 1  # second call hit the cache

    async def test_ttl_expiry_forces_refresh(
        self, mock_transport_factory
    ) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=VALID_ME_BODY)

        mock_transport_factory(handler)
        # TTL = 0 \u2192 immediately expired \u2192 every call re-hits.
        ins = _introspector(cache_ttl=0)

        await ins.introspect(
            cookie_header="gs-auth=tok", authorization_header=None
        )
        await ins.introspect(
            cookie_header="gs-auth=tok", authorization_header=None
        )
        assert call_count == 2

    async def test_401_is_not_cached(self, mock_transport_factory) -> None:
        # A rejected token must NEVER cache. If we cached 401s, a user
        # who fixes their session couldn't get back in without a TTL wait.
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(401)

        mock_transport_factory(handler)
        ins = _introspector(cache_ttl=30)

        r1 = await ins.introspect(
            cookie_header="gs-auth=tok", authorization_header=None
        )
        r2 = await ins.introspect(
            cookie_header="gs-auth=tok", authorization_header=None
        )
        assert r1 is None and r2 is None
        assert call_count == 2  # both calls hit CoreHub

    async def test_403_is_not_cached(self, mock_transport_factory) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(403)

        mock_transport_factory(handler)
        ins = _introspector(cache_ttl=30)

        await ins.introspect(
            cookie_header="gs-auth=tok", authorization_header=None
        )
        await ins.introspect(
            cookie_header="gs-auth=tok", authorization_header=None
        )
        assert call_count == 2

    async def test_invalidate_clears_cache(self, mock_transport_factory) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json=VALID_ME_BODY)

        mock_transport_factory(handler)
        ins = _introspector(cache_ttl=30)

        await ins.introspect(
            cookie_header="gs-auth=tok", authorization_header=None
        )
        await ins.invalidate()
        await ins.introspect(
            cookie_header="gs-auth=tok", authorization_header=None
        )
        assert call_count == 2


# --- Failure modes ----------------------------------------------------


@pytest.mark.asyncio
class TestFailureModes:
    async def test_no_credentials_returns_none_without_hitting_corehub(
        self, mock_transport_factory
    ) -> None:
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(500)  # would blow up if called

        mock_transport_factory(handler)
        ins = _introspector()

        result = await ins.introspect(
            cookie_header=None, authorization_header=None
        )
        assert result is None
        assert called is False

    async def test_missing_corehub_url_raises(self, mock_transport_factory) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=VALID_ME_BODY)

        mock_transport_factory(handler)
        ins = _introspector(corehub_url=None)

        with pytest.raises(IntrospectionError):
            await ins.introspect(
                cookie_header="gs-auth=tok", authorization_header=None
            )

    async def test_5xx_raises_introspection_error(
        self, mock_transport_factory
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="bad gateway")

        mock_transport_factory(handler)
        ins = _introspector()

        with pytest.raises(IntrospectionError):
            await ins.introspect(
                cookie_header="gs-auth=tok", authorization_header=None
            )

    async def test_non_json_body_raises(self, mock_transport_factory) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="not-json")

        mock_transport_factory(handler)
        ins = _introspector()

        with pytest.raises(IntrospectionError):
            await ins.introspect(
                cookie_header="gs-auth=tok", authorization_header=None
            )

    async def test_missing_role_raises(self, mock_transport_factory) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"username": "daniele"}  # no role key
            )

        mock_transport_factory(handler)
        ins = _introspector()

        with pytest.raises(IntrospectionError):
            await ins.introspect(
                cookie_header="gs-auth=tok", authorization_header=None
            )

    async def test_unknown_role_raises(self, mock_transport_factory) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"username": "daniele", "role": "OVERLORD"}
            )

        mock_transport_factory(handler)
        ins = _introspector()

        with pytest.raises(IntrospectionError):
            await ins.introspect(
                cookie_header="gs-auth=tok", authorization_header=None
            )

    async def test_transport_error_raises(self, mock_transport_factory) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        mock_transport_factory(handler)
        ins = _introspector()

        with pytest.raises(IntrospectionError):
            await ins.introspect(
                cookie_header="gs-auth=tok", authorization_header=None
            )
