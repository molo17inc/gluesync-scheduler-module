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

import os
import time
import json
import sys
import pytest
import requests
import subprocess
from unittest.mock import patch, MagicMock

# Add the project root directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Import mock gluesync_sdk before any other imports that might use it
from tests.mock_gluesync_sdk import GluesyncSDK
from datetime import datetime, timedelta

# Add the project root directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from play_pause import CoreHubClient, PipelineManager
from gluesync_sdk_client import GluesyncSDKClient


class MockResponse:
    def __init__(self, json_data, status_code=200):
        self.json_data = json_data
        self.status_code = status_code
        self.text = json.dumps(json_data)
    
    def json(self):
        return self.json_data


@pytest.fixture
def mock_sdk_client():
    """Create a mock SDK client for testing"""
    with patch('play_pause.gluesync_sdk_client') as mock_client:
        mock_client.is_initialized = True
        mock_client.token = "mock_token_123"
        mock_client.corehub_url = "http://localhost:8080"
        yield mock_client


@pytest.fixture
def mock_requests():
    """Mock requests module for testing CoreHub API calls"""
    with patch('requests.request') as mock_request:
        # Default response for successful API calls
        mock_request.return_value = MockResponse({"status": "success"})
        yield mock_request


def test_corehub_client_initialization(mock_sdk_client):
    """Test that CoreHubClient initializes correctly with SDK token"""
    # Mock the settings.CORE_HUB_URL
    with patch('play_pause.settings.CORE_HUB_URL', "http://localhost:8080"):
        client = CoreHubClient()
        assert client.token == "mock_token_123"
        assert client.base_url == "http://localhost:8080"


def test_play_entity_command(mock_sdk_client, mock_requests):
    """Test that play_entity sends the correct API request"""
    client = CoreHubClient()
    client.play_entity("test-pipeline", "test-entity", with_snapshot=True)
    
    # Check that the request was made with correct parameters
    mock_requests.assert_called_once()
    args, kwargs = mock_requests.call_args
    
    assert args[0] == "POST"
    assert "test-pipeline/commands/sync/start" in args[1]
    assert kwargs["params"]["entity"] == "test-entity"
    assert kwargs["params"]["withSnapshot"] == "true"
    assert kwargs["headers"]["Authorization"] == "Bearer mock_token_123"


def test_pause_entity_command(mock_sdk_client, mock_requests):
    """Test that pause_entity sends the correct API request"""
    client = CoreHubClient()
    client.pause_entity("test-pipeline", "test-entity")
    
    # Check that the request was made with correct parameters
    mock_requests.assert_called_once()
    args, kwargs = mock_requests.call_args
    
    assert args[0] == "POST"
    assert "test-pipeline/commands/sync/stop" in args[1]
    assert kwargs["params"]["entity"] == "test-entity"
    assert kwargs["headers"]["Authorization"] == "Bearer mock_token_123"


def test_resync_entity_command(mock_sdk_client, mock_requests):
    """Test that resync_entity sends the correct API request"""
    client = CoreHubClient()
    client.resync_entity("test-pipeline", "test-entity")
    
    # Check that the request was made with correct parameters
    mock_requests.assert_called_once()
    args, kwargs = mock_requests.call_args
    
    assert args[0] == "POST"
    assert "test-pipeline/commands/sync/resync" in args[1]
    assert kwargs["params"]["entity"] == "test-entity"
    assert kwargs["headers"]["Authorization"] == "Bearer mock_token_123"


def test_pipeline_manager_play_action(mock_sdk_client, mock_requests):
    """Test that PipelineManager correctly handles play action"""
    # Mock the get_entities method to return test entities
    with patch.object(CoreHubClient, 'get_entities', return_value=[
        {"entityId": "entity1", "entityName": "Entity 1"},
        {"entityId": "entity2", "entityName": "Entity 2"}
    ]):
        manager = PipelineManager()
        
        # Test play action for a specific entity
        manager.execute("play", "test-pipeline", ["entity1"], with_snapshot=True)
        
        # Check that play_entity was called with correct parameters
        mock_requests.assert_called_once()
        args, kwargs = mock_requests.call_args
        
        assert args[0] == "POST"
        assert "test-pipeline/commands/sync/start" in args[1]
        assert kwargs["params"]["entity"] == "entity1"
        assert kwargs["params"]["withSnapshot"] == "true"
        
        # Reset mock
        mock_requests.reset_mock()
        
        # Test play action for all entities in a pipeline
        manager.execute("play", "test-pipeline", with_snapshot=False)
        
        # Should have been called twice, once for each entity
        assert mock_requests.call_count == 2
        
        # Check first call (entity1)
        args, kwargs = mock_requests.call_args_list[0]
        assert args[0] == "POST"
        assert "test-pipeline/commands/sync/start" in args[1]
        assert kwargs["params"]["entity"] == "entity1"
        assert kwargs["params"]["withSnapshot"] == "false"
        
        # Check second call (entity2)
        args, kwargs = mock_requests.call_args_list[1]
        assert args[0] == "POST"
        assert "test-pipeline/commands/sync/start" in args[1]
        assert kwargs["params"]["entity"] == "entity2"
        assert kwargs["params"]["withSnapshot"] == "false"


def test_pipeline_manager_pause_action(mock_sdk_client, mock_requests):
    """Test that PipelineManager correctly handles pause action"""
    # Mock the get_entities method to return test entities
    with patch.object(CoreHubClient, 'get_entities', return_value=[
        {"entityId": "entity1", "entityName": "Entity 1"},
        {"entityId": "entity2", "entityName": "Entity 2"}
    ]):
        manager = PipelineManager()
        
        # Test pause action for a specific entity
        manager.execute("pause", "test-pipeline", ["entity1"])
        
        # Check that pause_entity was called with correct parameters
        mock_requests.assert_called_once()
        args, kwargs = mock_requests.call_args
        
        assert args[0] == "POST"
        assert "test-pipeline/commands/sync/stop" in args[1]
        assert kwargs["params"]["entity"] == "entity1"
        
        # Reset mock
        mock_requests.reset_mock()
        
        # Test pause action for all entities in a pipeline
        manager.execute("pause", "test-pipeline")
        
        # Should have been called twice, once for each entity
        assert mock_requests.call_count == 2
        
        # Check first call (entity1)
        args, kwargs = mock_requests.call_args_list[0]
        assert args[0] == "POST"
        assert "test-pipeline/commands/sync/stop" in args[1]
        assert kwargs["params"]["entity"] == "entity1"
        
        # Check second call (entity2)
        args, kwargs = mock_requests.call_args_list[1]
        assert args[0] == "POST"
        assert "test-pipeline/commands/sync/stop" in args[1]
        assert kwargs["params"]["entity"] == "entity2"


def test_pipeline_manager_resync_action(mock_sdk_client, mock_requests):
    """Test that PipelineManager correctly handles resync action"""
    # Mock the get_entities method to return test entities
    with patch.object(CoreHubClient, 'get_entities', return_value=[
        {"entityId": "entity1", "entityName": "Entity 1"},
        {"entityId": "entity2", "entityName": "Entity 2"}
    ]):
        manager = PipelineManager()
        
        # Test resync action for a specific entity
        manager.execute("resync", "test-pipeline", ["entity1"])
        
        # Check that resync_pipeline was called with correct parameters
        mock_requests.assert_called_once()
        args, kwargs = mock_requests.call_args
        
        assert args[0] == "POST"
        assert "test-pipeline/commands/sync/one-time-snapshot" in args[1]
        assert kwargs["params"]["entity"] == "entity1"
        assert kwargs["params"]["snapshotWriteMethod"] == "INSERT"
        
        # Reset mock
        mock_requests.reset_mock()
        
        # Test resync action for all entities in a pipeline
        manager.execute("resync", "test-pipeline")
        
        # Should have been called once for the entire pipeline
        mock_requests.assert_called_once()
        
        # Check the call for the entire pipeline
        args, kwargs = mock_requests.call_args
        assert args[0] == "POST"
        assert "test-pipeline/commands/sync/one-time-snapshot" in args[1]
        assert "entity" not in kwargs["params"]
        assert kwargs["params"]["snapshotWriteMethod"] == "INSERT"


if __name__ == "__main__":
    pytest.main(["-xvs", __file__])
