#!/usr/bin/env python3
"""
 * This file is part of Gluesync Scheduler Module (aka Chronos).
 *
 * Gluesync Scheduler Module (aka Chronos) is dual-licensed under the following licenses:
 *
 * 1. GNU General Public License (GPL) Version 3
 *    You may use, modify, and distribute this software under the terms of the GPL v3.
 *    See the LICENSE-GPL file or <http://www.gnu.org/licenses/gpl-3.0.html> for details.
 *    This option is available at no cost, but any derivative works must also be licensed under GPL v3.
 *
 * 2. MOLO17 Commercial License
 *    Alternatively, you may use this software under the MOLO17 Commercial License,
 *    which includes a warranty and permits proprietary use. Contact MOLO17 at info@molo17.com
 *    for licensing terms and conditions.
 *
 * You must choose one of these licenses to use this software. Using this software implies
 * acceptance of one of these licenses. See the accompanying LICENSE files or contact
 * MOLO17 for more information.
 *
 * Copyright (C) 2025 MOLO17. All rights reserved.
"""

"""
Periodic CoreHub connection health check.

A background ``asyncio`` task that calls CoreHub's
``GET /authentication/me`` every few minutes using the SDK client's
JWT token.  When the token is rejected (401) or CoreHub is
unreachable, the check automatically triggers a force-reconnect
of the SDK client so that scheduled jobs don't fail with a stale
token.

Configuration (environment variables):

* ``CHRONOS_HEALTH_CHECK_ENABLED`` — default ``true``; set to
  ``false`` to disable the health check entirely.
* ``CHRONOS_HEALTH_CHECK_INTERVAL_SECONDS`` — default ``300``
  (5 minutes).
* ``CHRONOS_HEALTH_CHECK_TIMEOUT_SECONDS`` — default ``10``
  (HTTP timeout per check).
"""

import asyncio
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r, falling back to default %d", name, raw, default)
        return default


class CoreHubHealthCheck:
    """Periodic health check that calls CoreHub ``GET /authentication/me``
    and triggers a force-reconnect when the connection is stale.

    The check is designed to run as a background ``asyncio`` task
    started from the FastAPI startup event and stopped during
    shutdown.  It uses ``httpx.AsyncClient`` (same pattern as
    ``CoreHubIntrospector``) so it never blocks the event loop.
    """

    #: CoreHub path for the authentication/identity endpoint.
    AUTH_ME_PATH = "/authentication/me"

    def __init__(self) -> None:
        self._enabled = _env_bool("CHRONOS_HEALTH_CHECK_ENABLED", True)
        self._interval = _env_int("CHRONOS_HEALTH_CHECK_INTERVAL_SECONDS", 300)
        self._timeout = _env_int("CHRONOS_HEALTH_CHECK_TIMEOUT_SECONDS", 10)
        self._verify_ssl = not (
            _env_bool("SSL_SKIP_VERIFY", False)
            or _env_bool("SKIP_TLS_VERIFICATION", False)
        )
        self._client: Optional[httpx.AsyncClient] = None
        self._task: Optional[asyncio.Task] = None
        self._stopped = False

    # --- Public API ---------------------------------------------------

    def start(self) -> None:
        """Start the background health-check task.

        If the health check is disabled via ``CHRONOS_HEALTH_CHECK_ENABLED``
        this is a no-op.
        """
        if not self._enabled:
            logger.info(
                "CoreHub health check is disabled (CHRONOS_HEALTH_CHECK_ENABLED=false)"
            )
            return
        if self._task is not None and not self._task.done():
            logger.warning("CoreHub health check is already running")
            return

        self._stopped = False
        self._task = asyncio.create_task(self._run())
        logger.info(
            "CoreHub health check task started (interval=%ds, timeout=%ds)",
            self._interval,
            self._timeout,
        )

    async def stop(self) -> None:
        """Stop the background health-check task and close the HTTP client."""
        self._stopped = True
        cancelled = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                cancelled = True
            finally:
                self._task = None

        if self._client is not None:
            await self._client.aclose()
            self._client = None
        logger.info("CoreHub health check stopped")

        if cancelled:
            raise asyncio.CancelledError()

    # --- Internal helpers --------------------------------------------

    def _get_corehub_url(self) -> Optional[str]:
        """Resolve the CoreHub base URL from the SDK client or env."""
        # Lazy import to avoid a circular dependency at module load time
        from gluesync_scheduler.core.gluesync_sdk_client import gluesync_sdk_client

        try:
            if (
                gluesync_sdk_client
                and gluesync_sdk_client.is_initialized
                and gluesync_sdk_client.corehub_url
            ):
                return gluesync_sdk_client.corehub_url
        except Exception as exc:
            logger.debug("Could not get CoreHub URL from SDK client: %s", exc)

        # Fallback to GLUESYNC_HOST env var
        return os.getenv("GLUESYNC_HOST", "") or None

    def _get_token(self) -> Optional[str]:
        """Get the current SDK JWT token."""
        from gluesync_scheduler.core.gluesync_sdk_client import gluesync_sdk_client

        try:
            if gluesync_sdk_client and gluesync_sdk_client.is_initialized:
                return gluesync_sdk_client.token
        except Exception as exc:
            logger.debug("Could not get token from SDK client: %s", exc)
        return None

    def _get_http_client(self) -> httpx.AsyncClient:
        """Return the reusable httpx client, constructing on first use."""
        if self._client is not None:
            return self._client
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        return self._client

    async def _check_once(self) -> None:
        """Perform a single health check tick."""
        from gluesync_scheduler.core.gluesync_sdk_client import gluesync_sdk_client

        # Skip if the SDK client is not ready yet
        if not gluesync_sdk_client or not gluesync_sdk_client.is_initialized:
            logger.debug("CoreHub health check: SDK client not initialized, skipping")
            return

        token = self._get_token()
        if not token:
            logger.debug("CoreHub health check: no SDK token available, skipping")
            return

        base_url = self._get_corehub_url()
        if not base_url:
            logger.debug("CoreHub health check: CoreHub URL not available, skipping")
            return

        url = base_url.rstrip("/") + self.AUTH_ME_PATH
        headers = {"Authorization": f"Bearer {token}"}

        client = self._get_http_client()
        try:
            response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("CoreHub health check: unreachable (%s)", exc)
            await self._trigger_reconnect()
            return

        if response.status_code == 200:
            logger.debug("CoreHub health check passed")
            return

        if response.status_code in (401, 403):
            logger.warning(
                "CoreHub health check failed: token rejected (%d)",
                response.status_code,
            )
            await self._trigger_reconnect()
            return

        if response.status_code >= 500:
            # Server-side issue — our token is likely fine; don't reconnect
            logger.warning(
                "CoreHub health check: server error (%d)", response.status_code
            )
            return

        # Other unexpected status codes — log but don't reconnect
        logger.warning(
            "CoreHub health check: unexpected status %d", response.status_code
        )

    async def _trigger_reconnect(self) -> None:
        """Force the SDK client to disconnect and perform a fresh login."""
        from gluesync_scheduler.core.gluesync_sdk_client import gluesync_sdk_client

        try:
            logger.info("CoreHub health check: triggering SDK force-reconnect...")
            await gluesync_sdk_client.initialize(force_reconnect=True)
            logger.info("CoreHub health check: SDK force-reconnect completed")
        except Exception:
            logger.exception("CoreHub health check: force-reconnect failed")

    async def _run(self) -> None:
        """Main loop — runs until ``stop()`` is called."""
        logger.debug(
            "CoreHub health check loop started (interval=%ds)", self._interval
        )
        while not self._stopped:
            try:
                await self._check_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Log and continue — the health check must not crash the loop
                logger.exception("Unexpected error in CoreHub health check loop")
                continue

            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                raise

        # Clean up the HTTP client when the loop exits
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# --- Singleton ---------------------------------------------------------

#: Process-wide singleton, started/stopped from ``app.py``.
health_check = CoreHubHealthCheck()
