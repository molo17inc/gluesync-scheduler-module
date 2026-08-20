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
from gluesync_scheduler.core.gluesync_sdk_client import GluesyncSDKClient


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
    # Mock the settings.GLUESYNC_HOST
    with patch('play_pause.settings.GLUESYNC_HOST', "http://localhost:8080"):
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
    assert "test-pipeline/commands/sync/redo" in args[1]
    assert kwargs["params"]["entity"] == "test-entity"
    assert kwargs["params"]["withSnapshot"] == "true"
    assert kwargs["params"]["snapshotWriteMethod"] == "UPSERT"
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
        assert "test-pipeline/commands/sync/redo" in args[1]
        assert kwargs["params"]["entity"] == "entity1"
        assert kwargs["params"]["withSnapshot"] == "true"
        assert kwargs["params"]["snapshotWriteMethod"] == "UPSERT"
        
        # Reset mock
        mock_requests.reset_mock()
        
        # Test resync action for all entities in a pipeline
        manager.execute("resync", "test-pipeline")
        
        # Should have been called once for the entire pipeline
        mock_requests.assert_called_once()
        
        # Check the call for the entire pipeline
        args, kwargs = mock_requests.call_args
        assert args[0] == "POST"
        assert "test-pipeline/commands/sync/redo" in args[1]
        assert "entity" not in kwargs["params"]
        assert kwargs["params"]["withSnapshot"] == "true"
        assert kwargs["params"]["snapshotWriteMethod"] == "UPSERT"


def test_job_service_group_redo_action(mock_sdk_client):
    """Test that JobService._execute_group_operation correctly handles redo-group action"""
    from unittest.mock import MagicMock, patch
    import sys

    # Pre-load a mock scheduler_service module to prevent real initialization
    mock_scheduler_module = MagicMock()
    mock_scheduler_module.scheduler_service = MagicMock()
    sys.modules['gluesync_scheduler.services.scheduler_service'] = mock_scheduler_module

    from gluesync_scheduler.services.job_service import JobService
    from gluesync_scheduler.models.models import TaskType, ScheduledJob

    mock_db = MagicMock()
    job_service = JobService(db=mock_db)

    mock_job = MagicMock(spec=ScheduledJob)
    mock_job.pipeline_id = "test-pipeline"
    mock_job.group_ids = '["group-123"]'
    mock_job.with_snapshot = True
    mock_job.snapshot_write_method = "INSERT"
    mock_job.task_type = TaskType.GROUP_REDO

    with patch('gluesync_scheduler.core.play_pause.CoreHubClient') as mock_client_class:
        mock_instance = MagicMock()
        mock_instance.redo_group.return_value = True
        mock_client_class.return_value = mock_instance

        success, message, details = job_service._execute_group_operation(
            job=mock_job,
            group_ids=["group-123"],
            action="redo-group"
        )

        mock_instance.redo_group.assert_called_once_with(
            "test-pipeline",
            "group-123",
            with_snapshot=True,
            snapshot_write_method="INSERT"
        )

        assert success is True
        assert "Successfully executed redo-group for all 1 groups" in message
        assert details["total_groups"] == 1
        assert details["successful_groups"] == 1


def test_job_service_group_redo_action_failure(mock_sdk_client):
    """Test that JobService._execute_group_operation correctly handles redo-group failures"""
    from unittest.mock import MagicMock, patch
    import sys

    mock_scheduler_module = MagicMock()
    mock_scheduler_module.scheduler_service = MagicMock()
    sys.modules['gluesync_scheduler.services.scheduler_service'] = mock_scheduler_module

    from gluesync_scheduler.services.job_service import JobService
    from gluesync_scheduler.models.models import TaskType, ScheduledJob

    mock_db = MagicMock()
    job_service = JobService(db=mock_db)

    mock_job = MagicMock(spec=ScheduledJob)
    mock_job.pipeline_id = "test-pipeline"
    mock_job.group_ids = '["group-456"]'
    mock_job.with_snapshot = False
    mock_job.snapshot_write_method = "UPSERT"
    mock_job.task_type = TaskType.GROUP_REDO

    with patch('gluesync_scheduler.core.play_pause.CoreHubClient') as mock_client_class:
        mock_instance = MagicMock()
        mock_instance.redo_group.return_value = False
        mock_client_class.return_value = mock_instance

        success, message, details = job_service._execute_group_operation(
            job=mock_job,
            group_ids=["group-456"],
            action="redo-group"
        )

        mock_instance.redo_group.assert_called_once_with(
            "test-pipeline",
            "group-456",
            with_snapshot=False,
            snapshot_write_method="UPSERT"
        )

        assert success is False
        assert "Executed redo-group for 0/1 groups successfully" in message
        assert details["total_groups"] == 1
        assert details["successful_groups"] == 0


if __name__ == "__main__":
    pytest.main(["-xvs", __file__])
