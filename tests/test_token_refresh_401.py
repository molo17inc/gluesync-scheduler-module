# -*- coding: utf-8 -*-

"""
Unit tests for the GSSD-1195 Token Refresh on 401 requirement.
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from gluesync_scheduler.core.play_pause import CoreHubClient
from gluesync_scheduler.core.gluesync_sdk_client import GluesyncSDKClient

@pytest.fixture
def clean_sdk_and_client():
    """Reset the singleton instance of GluesyncSDKClient and CoreHubClient for testing."""
    GluesyncSDKClient._instance = None
    GluesyncSDKClient._is_initialized = False
    GluesyncSDKClient._initializing = False
    GluesyncSDKClient._token = None
    GluesyncSDKClient._client = None
    
    CoreHubClient._instance = None
    CoreHubClient._discovered_url = "http://localhost:1717"
    
    # Set default environment variable values
    os.environ['CHRONOS_SDK_TOKEN_REFRESH_ON_401'] = 'True'

def test_token_refresh_on_401_success(clean_sdk_and_client):
    """
    Test that a 401 response from CoreHub triggers a token refresh and retry,
    returning a successful response on the second attempt (retry).
    """
    # 1. Setup mock SDK and HTTP responses
    mock_sdk_client = GluesyncSDKClient.get_instance()
    mock_sdk_client._is_initialized = True
    mock_sdk_client._token = "expired_token"
    
    # Setup the mock gluesync_sdk.GluesyncClient
    mock_client = MagicMock()
    mock_client.token = "fresh_token_123"
    mock_client.is_connected = True
    
    # 2. Setup mock requests to CoreHub
    # First response: 401 Unauthorized
    # Second response: 200 OK (Retry)
    mock_response_401 = MagicMock()
    mock_response_401.status_code = 401
    mock_response_401.json.return_value = {"message": "Invalid authorization token"}
    
    mock_response_200 = MagicMock()
    mock_response_200.status_code = 200
    mock_response_200.text = '{"status": "success", "message": "Pipeline started"}'
    mock_response_200.json.return_value = {"status": "success", "message": "Pipeline started"}
    
    # Mock initialize to be a non-async or awaitable that does not crash on MagicMock await
    async def mock_initialize(**kwargs):
        pass

    with patch('gluesync_scheduler.core.gluesync_sdk_client.GluesyncClient', return_value=mock_client), \
         patch('requests.post', side_effect=[mock_response_401, mock_response_200]) as mock_post, \
         patch.object(GluesyncSDKClient, 'initialize', side_effect=mock_initialize), \
         patch.object(CoreHubClient, '_get_current_token', side_effect=[("expired_token", True), ("fresh_token_123", True)]):
        
        client = CoreHubClient()
        response = client.fetch_core_hub("/pipelines/test/commands/sync/start", method="POST")
        
        # Verify first call used expired token, second used fresh token
        assert response is not None
        assert response["status"] == "success"
        assert mock_post.call_count == 2
        
        # Check Authorization headers (with dict mutation in retry block, headers is modified in place,
        # so we check mock_post arguments at each call index)
        first_headers = mock_post.call_args_list[0][1]['headers']
        second_headers = mock_post.call_args_list[1][1]['headers']
        assert first_headers['Authorization'] == 'Bearer expired_token'
        assert second_headers['Authorization'] == 'Bearer fresh_token_123'

def test_token_refresh_on_401_failure_propagates_401(clean_sdk_and_client):
    """
    Test that if CoreHub still returns a 401 after the token refresh,
    it does not retry further and propagates the 401 auth error cleanly.
    """
    mock_sdk_client = GluesyncSDKClient.get_instance()
    mock_sdk_client._is_initialized = True
    mock_sdk_client._token = "expired_token"
    
    mock_client = MagicMock()
    mock_client.token = "refreshed_but_still_rejected"
    mock_client.is_connected = True
    
    mock_response_401_expired = MagicMock()
    mock_response_401_expired.status_code = 401
    mock_response_401_expired.json.return_value = {"message": "Invalid authorization token"}
    
    mock_response_401_still_invalid = MagicMock()
    mock_response_401_still_invalid.status_code = 401
    mock_response_401_still_invalid.json.return_value = {"message": "Invalid authorization token"}
    
    async def mock_initialize(**kwargs):
        pass

    with patch('gluesync_scheduler.core.gluesync_sdk_client.GluesyncClient', return_value=mock_client), \
         patch('requests.post', side_effect=[mock_response_401_expired, mock_response_401_still_invalid]) as mock_post, \
         patch.object(GluesyncSDKClient, 'initialize', side_effect=mock_initialize), \
         patch.object(CoreHubClient, '_get_current_token', side_effect=[("expired_token", True), ("refreshed_but_still_rejected", True)]):
        
        client = CoreHubClient()
        response = client.fetch_core_hub("/pipelines/test/commands/sync/start", method="POST")
        
        assert response is not None
        assert response["status"] == "error"
        assert response["status_code"] == 401
        assert "CoreHub rejected the refreshed token" in response["message"]
        assert mock_post.call_count == 2

def test_first_try_200_no_refresh(clean_sdk_and_client):
    """
    Test that when the first try returns 200, no token refresh or retry is performed.
    """
    mock_sdk_client = GluesyncSDKClient.get_instance()
    mock_sdk_client._is_initialized = True
    mock_sdk_client._token = "valid_token"
    
    mock_response_200 = MagicMock()
    mock_response_200.text = '{"status": "success", "message": "Pipeline started"}'
    mock_response_200.status_code = 200
    mock_response_200.json.return_value = {"status": "success", "message": "Pipeline started"}
    
    with patch('requests.post', return_value=mock_response_200) as mock_post, \
         patch.object(CoreHubClient, '_get_current_token', return_value=("valid_token", True)):
        client = CoreHubClient()
        response = client.fetch_core_hub("/pipelines/test/commands/sync/start", method="POST")
        
        assert response is not None
        assert response["status"] == "success"
        assert mock_post.call_count == 1

def test_non_auth_error_not_retried(clean_sdk_and_client):
    """
    Test that non-auth errors (e.g., 500) are not retried.
    """
    mock_sdk_client = GluesyncSDKClient.get_instance()
    mock_sdk_client._is_initialized = True
    mock_sdk_client._token = "valid_token"

    mock_response_500 = MagicMock()
    mock_response_500.status_code = 500
    mock_response_500.text = "Internal Server Error"

    with patch('requests.post', return_value=mock_response_500) as mock_post, \
         patch.object(CoreHubClient, '_get_current_token', return_value=("valid_token", True)):
        client = CoreHubClient()
        response = client.fetch_core_hub("/pipelines/test/commands/sync/start", method="POST")

        assert response is not None
        assert response["status"] == "error"
        assert response["status_code"] == 500
        assert mock_post.call_count == 1


def test_force_reconnect_clears_old_client(clean_sdk_and_client):
    """
    Verify that force_reconnect=True disconnects and clears the old client
    so that initialize() creates a fresh client instead of reusing a stale one.
    """
    import asyncio

    mock_sdk_client = GluesyncSDKClient.get_instance()
    mock_sdk_client._is_initialized = True
    mock_sdk_client._token = "stale_token"

    # Simulate the old client that is still "connected" with a stale token
    mock_old_client = MagicMock()
    mock_old_client.is_connected = True
    mock_old_client.token = "stale_token"

    disconnect_called = []

    async def mock_disconnect():
        disconnect_called.append(True)

    mock_old_client.disconnect = mock_disconnect
    mock_sdk_client._client = mock_old_client

    # The new client that GluesyncClient(...) will return
    mock_new_client = MagicMock()
    mock_new_client.is_connected = True
    mock_new_client.token = "fresh_token"
    mock_new_client.host = "localhost"
    mock_new_client.port = 1717

    async def mock_connect():
        # Simulate the on_connected callback firing
        await mock_sdk_client._on_connected("fresh_token")

    mock_new_client.connect = mock_connect

    with patch('gluesync_scheduler.core.gluesync_sdk_client.GluesyncClient', return_value=mock_new_client), \
         patch.dict(os.environ, {
             'SSL_ENABLED': 'false',
             'GLUESYNC_HOST': 'http://localhost:1717',
             'GLUESYNC_MODULE_TAG': 'chronos',
         }):
        asyncio.run(mock_sdk_client.initialize(force_reconnect=True))

        # Old client was disconnected
        assert len(disconnect_called) == 1, "Old client should have been disconnected"
        # Old client reference was cleared and replaced with the new one
        assert mock_sdk_client._client is mock_new_client, "Should have created a new client"
        # Token was refreshed from the new client
        assert mock_sdk_client._token == "fresh_token", "Should have a fresh token"
        assert mock_sdk_client._is_initialized is True, "Should be marked as initialized"


def test_refresh_and_retry_warns_on_identical_token(clean_sdk_and_client):
    """
    Verify that _refresh_and_retry logs a warning when the 'refreshed' token
    is identical to the one that was rejected.
    """
    mock_sdk_client = GluesyncSDKClient.get_instance()
    mock_sdk_client._is_initialized = True
    mock_sdk_client._token = "stale_token"

    mock_response_401 = MagicMock()
    mock_response_401.status_code = 401
    mock_response_401.json.return_value = {"message": "Invalid authorization token"}

    mock_response_401_retry = MagicMock()
    mock_response_401_retry.status_code = 401
    mock_response_401_retry.json.return_value = {"message": "Invalid authorization token"}

    async def mock_initialize(**kwargs):
        pass

    with patch('requests.post', side_effect=[mock_response_401, mock_response_401_retry]) as mock_post, \
         patch.object(GluesyncSDKClient, 'initialize', side_effect=mock_initialize), \
         patch.object(CoreHubClient, '_get_current_token', side_effect=[("stale_token", True), ("stale_token", True)]):
        client = CoreHubClient()
        with patch.object(client, '_reinitialize_sdk_client') as mock_reinit:
            response = client.fetch_core_hub("/pipelines/test/commands/sync/start", method="POST")

            assert response is not None
            assert response["status"] == "error"
            assert response["status_code"] == 401
            # _reinitialize_sdk_client should have been called
            mock_reinit.assert_called_once()
