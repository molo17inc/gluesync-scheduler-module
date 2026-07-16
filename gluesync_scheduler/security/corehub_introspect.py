"""
CoreHub introspection client.

Given the ``Cookie`` / ``Authorization`` header from an incoming
request, calls CoreHub's ``GET /auth/me`` to resolve the caller's
identity + role. Successful lookups are cached for a short TTL to
keep per-request latency down; failures are never cached.

See ``docs/auth-plan.md`` \u00a72.5 and \u00a74.3 for the full design.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from gluesync_scheduler.security.exceptions import IntrospectionError
from gluesync_scheduler.security.user_role import UserRole

logger = logging.getLogger(__name__)


# --- Data model --------------------------------------------------------


@dataclass(frozen=True)
class CurrentUser:
    """The identity of the caller for a single request."""

    username: str
    role: UserRole


# --- Configuration -----------------------------------------------------


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "Invalid %s=%r, falling back to default %d", name, raw, default
        )
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "Invalid %s=%r, falling back to default %f", name, raw, default
        )
        return default


# --- Introspector -----------------------------------------------------


class CoreHubIntrospector:
    """Resolves an incoming caller's identity via CoreHub ``GET /auth/me``.

    Instances hold a short-TTL cache keyed by the raw token string
    (cookie value or Bearer blob). Cache hits skip the HTTP round-trip.

    The introspector is safe to share across concurrent requests. A
    single lock protects the cache dict against write races.
    """

    #: CoreHub path fragment appended to the discovered base URL.
    AUTH_ME_PATH = "/authentication/me"

    #: Cookie name used by CoreHub for the HttpOnly JWT cookie.
    AUTH_COOKIE_NAME = "gs-auth"

    def __init__(
        self,
        *,
        corehub_url_provider,
        cache_ttl_seconds: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
        verify_ssl: bool = True,
    ) -> None:
        """
        Args:
            corehub_url_provider: zero-arg callable returning the current
                CoreHub base URL (e.g. ``lambda: gluesync_sdk_client.corehub_url``).
                Called on every miss to pick up late URL discovery + rotations.
            cache_ttl_seconds: TTL for successful lookups. Defaults to
                ``CHRONOS_AUTH_CACHE_TTL`` env var, then 30.
            timeout_seconds: httpx timeout. Defaults to
                ``CHRONOS_AUTH_TIMEOUT_MS`` env var (millis), then 5.0s.
            verify_ssl: passed to ``httpx.AsyncClient``. Callers that
                need to disable TLS verification (dev, self-signed
                CoreHub) should pass ``False`` explicitly.
        """
        self._corehub_url_provider = corehub_url_provider
        self._cache_ttl = (
            cache_ttl_seconds
            if cache_ttl_seconds is not None
            else _env_int("CHRONOS_AUTH_CACHE_TTL", 30)
        )
        timeout_ms_default = int(_env_float("CHRONOS_AUTH_TIMEOUT_MS", 5000.0))
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else timeout_ms_default / 1000.0
        )
        self._verify_ssl = verify_ssl
        self._cache: dict[str, tuple[CurrentUser, float]] = {}
        self._cache_lock = asyncio.Lock()
        # Lazily-materialised httpx client, reused across requests to
        # amortise connection setup. Bound to the event loop of the
        # first ``introspect()`` call.
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()

    # --- Public API ---------------------------------------------------

    async def introspect(
        self,
        *,
        cookie_header: Optional[str],
        authorization_header: Optional[str],
    ) -> Optional[CurrentUser]:
        """Resolve caller identity from the incoming request headers.

        Returns:
            ``CurrentUser`` on success, ``None`` when CoreHub explicitly
            rejects the caller (401/403). Raises ``IntrospectionError``
            for transport / server errors so the calling layer can
            distinguish "definitely unauthenticated" from "couldn't ask".

        The caller (FastAPI ``Depends``) should treat both ``None`` and
        ``IntrospectionError`` as fail-closed 401 to the client.
        """
        cache_key = self._cache_key(
            cookie_header=cookie_header,
            authorization_header=authorization_header,
        )
        if cache_key is None:
            # No credentials at all \u2014 don't even bother CoreHub.
            return None

        # Fast path: cache hit.
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        base = self._corehub_url_provider()
        if not base:
            # SDK not warmed up yet. Fail closed.
            raise IntrospectionError(
                "CoreHub URL not yet discovered; auth cannot be verified"
            )

        url = base.rstrip("/") + self.AUTH_ME_PATH
        headers: dict[str, str] = {}
        if authorization_header:
            headers["Authorization"] = authorization_header
        if cookie_header:
            headers["Cookie"] = cookie_header

        client = await self._get_client()
        try:
            response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("CoreHub introspection transport error: %s", exc)
            raise IntrospectionError(f"CoreHub /auth/me unreachable: {exc}") from exc

        if response.status_code in (401, 403):
            # Definite rejection \u2014 do NOT cache. Retries must re-check.
            return None
        if response.status_code >= 500:
            raise IntrospectionError(
                f"CoreHub /auth/me returned {response.status_code}"
            )
        if response.status_code != 200:
            raise IntrospectionError(
                f"CoreHub /auth/me returned unexpected status {response.status_code}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise IntrospectionError(
                f"CoreHub /auth/me returned non-JSON body: {exc}"
            ) from exc

        username = body.get("username")
        role_str = body.get("role")
        role = UserRole.parse(role_str)
        if not username or role is None:
            raise IntrospectionError(
                "CoreHub /auth/me response missing username/role "
                f"(role={role_str!r})"
            )

        user = CurrentUser(username=username, role=role)
        await self._put_cached(cache_key, user)
        return user

    # --- Internal helpers --------------------------------------------

    def _cache_key(
        self,
        *,
        cookie_header: Optional[str],
        authorization_header: Optional[str],
    ) -> Optional[str]:
        """Derive a cache key from the incoming credentials.

        We key on the raw token bytes so two different sessions for the
        same user cache independently (one may be revoked and the other
        still live).
        """
        if authorization_header:
            # e.g. "Bearer eyJ..." \u2014 keep the whole string, including scheme.
            token = authorization_header.strip()
            if token:
                return f"authz:{token}"

        if cookie_header:
            # Extract the gs-auth value out of a Cookie header that may
            # carry unrelated cookies. Simple parse: name=value; ...
            for chunk in cookie_header.split(";"):
                name, _, value = chunk.strip().partition("=")
                if name == self.AUTH_COOKIE_NAME and value:
                    return f"cookie:{value}"

        return None

    def _get_cached(self, key: str) -> Optional[CurrentUser]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        user, expires_at = entry
        if expires_at <= time.monotonic():
            # Lazy eviction; safe to leave stale keys until touched.
            return None
        return user

    async def _put_cached(self, key: str, user: CurrentUser) -> None:
        expires_at = time.monotonic() + self._cache_ttl
        async with self._cache_lock:
            self._cache[key] = (user, expires_at)

    # --- Test / ops helpers ------------------------------------------

    async def invalidate(self) -> None:
        """Wipe the introspection cache. Intended for tests / ops tools."""
        async with self._cache_lock:
            self._cache.clear()

    async def aclose(self) -> None:
        """Shut down the underlying httpx client.

        Called from the FastAPI shutdown hook. Safe to call multiple
        times.
        """
        async with self._client_lock:
            if self._client is not None:
                await self._client.aclose()
                self._client = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Return the reusable httpx client, constructing on first use."""
        if self._client is not None:
            return self._client
        async with self._client_lock:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    timeout=self._timeout_seconds,
                    verify=self._verify_ssl,
                )
            return self._client


# --- Singleton ---------------------------------------------------------

_singleton: Optional[CoreHubIntrospector] = None


def get_introspector() -> CoreHubIntrospector:
    """Return the process-wide introspector, creating it on first use.

    Lazy so that unit tests can override the singleton before it's
    materialised (via ``set_introspector``) and so that the CoreHub URL
    provider isn't captured until the SDK has had a chance to start.
    """
    global _singleton
    if _singleton is None:
        _singleton = _build_default_introspector()
    return _singleton


def set_introspector(introspector: Optional[CoreHubIntrospector]) -> None:
    """Replace / reset the process-wide introspector.

    Tests use this to inject a mock. Passing ``None`` resets to lazy
    default.
    """
    global _singleton
    _singleton = introspector


def _build_default_introspector() -> CoreHubIntrospector:
    # Import here to avoid a startup cycle: the SDK client imports
    # logging config, which may not be ready when this module first
    # loads.
    from gluesync_scheduler.core.gluesync_sdk_client import gluesync_sdk_client

    def _url_provider() -> Optional[str]:
        override = os.getenv("CHRONOS_COREHUB_URL_OVERRIDE")
        if override:
            return override
        return getattr(gluesync_sdk_client, "corehub_url", None)

    verify_ssl = os.getenv("SKIP_TLS_VERIFICATION", "").lower() not in (
        "1",
        "true",
        "yes",
    ) and os.getenv("SSL_SKIP_VERIFY", "").lower() not in (
        "1",
        "true",
        "yes",
    )
    return CoreHubIntrospector(
        corehub_url_provider=_url_provider,
        verify_ssl=verify_ssl,
    )
