#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Unit tests for the Gluesync SDK Client wrapper (Chronos connection manager).
Specifically tests:
1. Concurrent initialization deduplication using the _initializing state lock.
2. Exponential backoff and retry logic under Connection errors.
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock
from gluesync_scheduler.core.gluesync_sdk_client import GluesyncSDKClient
from gluesync_sdk.exceptions import GluesyncConnectionError

@pytest.mark.asyncio
async def test_sdk_client_initialization_deduplication():
    """
    Test that concurrent calls to GluesyncSDKClient().initialize()
    are deduplicated using the _initializing state lock and do not trigger
    duplicate client recreation or connections.
    """
    # Reset singleton state for a clean test
    GluesyncSDKClient._instance = None
    GluesyncSDKClient._is_initialized = False
    GluesyncSDKClient._initializing = False
    GluesyncSDKClient._token = None
    GluesyncSDKClient._client = None
    
    client_instance = GluesyncSDKClient()
    
    # Mock GluesyncClient and its connect method
    mock_client = MagicMock()
    async def slow_connect():
        await asyncio.sleep(0.1)  # Simulate connection taking time
        client_instance._token = "some_token"
        mock_client.token = "some_token"
        
    mock_client.connect = slow_connect
    mock_client.is_connected = True
    mock_client.host = "localhost"
    mock_client.port = 1717
    
    with patch('gluesync_scheduler.core.gluesync_sdk_client.GluesyncClient', return_value=mock_client) as mock_class:
        # Run concurrent calls to initialize
        await asyncio.gather(
            client_instance.initialize(),
            client_instance.initialize(),
            client_instance.initialize()
        )
        
        # Verify GluesyncClient was instantiated exactly once
        mock_class.assert_called_once()

@pytest.mark.asyncio
async def test_sdk_client_exponential_backoff_retry():
    """
    Test that GluesyncSDKClient().initialize() retries with exponential backoff
    on GluesyncConnectionError, doubling the backoff as expected.
    """
    # Reset singleton state for a clean test
    GluesyncSDKClient._instance = None
    GluesyncSDKClient._is_initialized = False
    GluesyncSDKClient._initializing = False
    GluesyncSDKClient._token = None
    GluesyncSDKClient._client = None
    
    client_instance = GluesyncSDKClient()
    
    mock_client = MagicMock()
    
    connect_attempts = 0
    async def connect_stub():
        nonlocal connect_attempts
        connect_attempts += 1
        if connect_attempts < 3:
            raise GluesyncConnectionError("CoreHub offline")
        # Succeed on 3rd attempt
        client_instance._token = "valid_token"
        mock_client.token = "valid_token"
        
    mock_client.connect = connect_stub
    mock_client.is_connected = True
    mock_client.host = "localhost"
    mock_client.port = 1717
    
    # Track sleep delays to assert exponential backoff
    sleep_calls = []
    async def sleep_stub(delay):
        sleep_calls.append(delay)
        # Empty coroutine behaves as instantaneous sleep without recursion
        pass
        
    with patch('gluesync_scheduler.core.gluesync_sdk_client.GluesyncClient', return_value=mock_client), \
         patch('asyncio.sleep', side_effect=sleep_stub):
         
        await client_instance.initialize()
        
        # Connect should be called 3 times total
        assert connect_attempts == 3
        # Sleep should be called twice (before the 2nd and 3rd connect attempts)
        assert len(sleep_calls) == 2
        # Backoff delays should be exponential: 1, then 2
        assert sleep_calls == [1, 2]
        # Successfully initialized
        assert client_instance._is_initialized is True
        assert client_instance._token == "valid_token"
