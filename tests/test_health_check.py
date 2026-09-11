# -*- coding: utf-8 -*-

"""
Unit tests for the CoreHub health check background task.
"""

import asyncio
import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from gluesync_scheduler.core.health_check import CoreHubHealthCheck
from gluesync_scheduler.core.gluesync_sdk_client import GluesyncSDKClient
import gluesync_scheduler.core.gluesync_sdk_client as sdk_module


@pytest.fixture
def clean_sdk_and_health_check():
    """Reset the SDK singleton state and create a fresh health check instance.

    The module-level ``gluesync_sdk_client`` singleton is reused (not
    replaced) so that ``_check_once``'s ``from ... import gluesync_sdk_client``
    picks up the same object we configure here.
    """
    sdk = sdk_module.gluesync_sdk_client
    sdk._is_initialized = False
    sdk._initializing = False
    sdk._token = None
    sdk._client = None

    # Ensure default env values
    os.environ.pop("CHRONOS_HEALTH_CHECK_ENABLED", None)
    os.environ.pop("CHRONOS_HEALTH_CHECK_INTERVAL_SECONDS", None)
    os.environ.pop("CHRONOS_HEALTH_CHECK_TIMEOUT_SECONDS", None)

    hc = CoreHubHealthCheck()
    hc._stopped = False
    hc._client = None
    hc._task = None
    return hc


def _setup_mock_sdk(token="valid_token", initialized=True):
    """Configure the existing module-level SDK singleton with mock state."""
    sdk = sdk_module.gluesync_sdk_client
    sdk._is_initialized = initialized
    sdk._token = token
    sdk._client = MagicMock()
    sdk._client.host = "localhost"
    sdk._client.port = 1717
    return sdk


@pytest.mark.asyncio
async def test_health_check_pass_on_200(clean_sdk_and_health_check):
    """A 200 response should log success and NOT trigger a reconnect."""
    hc = clean_sdk_and_health_check
    _setup_mock_sdk()

    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_http_client = MagicMock()
    mock_http_client.get = AsyncMock(return_value=mock_response)

    with patch.object(hc, "_get_http_client", AsyncMock(return_value=mock_http_client)), \
         patch.object(GluesyncSDKClient, "initialize", new_callable=AsyncMock) as mock_init:
        await hc._check_once()

        mock_http_client.get.assert_called_once()
        mock_init.assert_not_called()


@pytest.mark.asyncio
async def test_health_check_reconnect_on_401(clean_sdk_and_health_check):
    """A 401 response should trigger a force-reconnect."""
    hc = clean_sdk_and_health_check
    _setup_mock_sdk()

    mock_response = MagicMock()
    mock_response.status_code = 401

    mock_http_client = MagicMock()
    mock_http_client.get = AsyncMock(return_value=mock_response)

    with patch.object(hc, "_get_http_client", AsyncMock(return_value=mock_http_client)), \
         patch.object(GluesyncSDKClient, "initialize", new_callable=AsyncMock) as mock_init:
        await hc._check_once()

        mock_http_client.get.assert_called_once()
        mock_init.assert_called_once_with(force_reconnect=True)


@pytest.mark.asyncio
async def test_health_check_reconnect_on_network_error(clean_sdk_and_health_check):
    """A network error (httpx.ConnectError) should trigger a force-reconnect."""
    import httpx

    hc = clean_sdk_and_health_check
    _setup_mock_sdk()

    mock_http_client = MagicMock()
    mock_http_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

    with patch.object(hc, "_get_http_client", AsyncMock(return_value=mock_http_client)), \
         patch.object(GluesyncSDKClient, "initialize", new_callable=AsyncMock) as mock_init:
        await hc._check_once()

        mock_http_client.get.assert_called_once()
        mock_init.assert_called_once_with(force_reconnect=True)


@pytest.mark.asyncio
async def test_health_check_no_reconnect_on_500(clean_sdk_and_health_check):
    """A 5xx response should NOT trigger a reconnect (server-side issue)."""
    hc = clean_sdk_and_health_check
    _setup_mock_sdk()

    mock_response = MagicMock()
    mock_response.status_code = 503

    mock_http_client = MagicMock()
    mock_http_client.get = AsyncMock(return_value=mock_response)

    with patch.object(hc, "_get_http_client", AsyncMock(return_value=mock_http_client)), \
         patch.object(GluesyncSDKClient, "initialize", new_callable=AsyncMock) as mock_init:
        await hc._check_once()

        mock_http_client.get.assert_called_once()
        mock_init.assert_not_called()


@pytest.mark.asyncio
async def test_health_check_skips_when_sdk_not_initialized(clean_sdk_and_health_check):
    """When the SDK client is not initialized, the health check should skip."""
    hc = clean_sdk_and_health_check
    _setup_mock_sdk(initialized=False, token=None)

    mock_http_client = MagicMock()
    mock_http_client.get = AsyncMock()

    with patch.object(hc, "_get_http_client", AsyncMock(return_value=mock_http_client)), \
         patch.object(GluesyncSDKClient, "initialize", new_callable=AsyncMock) as mock_init:
        await hc._check_once()

        mock_http_client.get.assert_not_called()
        mock_init.assert_not_called()


@pytest.mark.asyncio
async def test_health_check_disabled_by_env(clean_sdk_and_health_check):
    """When CHRONOS_HEALTH_CHECK_ENABLED=false, start() should be a no-op."""
    with patch.dict(os.environ, {"CHRONOS_HEALTH_CHECK_ENABLED": "false"}):
        hc = CoreHubHealthCheck()
        assert hc._enabled is False

        await hc.start()

        # No task should have been created
        assert hc._task is None


@pytest.mark.asyncio
async def test_health_check_stop_cancels_task(clean_sdk_and_health_check):
    """stop() should cancel the background task and close the HTTP client."""
    hc = clean_sdk_and_health_check

    # Mock the _run method so the task completes quickly
    async def mock_run():
        await asyncio.sleep(0.01)

    with patch.object(hc, "_run", mock_run):
        await hc.start()
        assert hc._task is not None

        # Wait for the mock run to complete
        await asyncio.sleep(0.05)

    # Now stop — should handle gracefully even if task already completed
    await hc.stop()
    assert hc._task is None
    assert hc._stopped is True
