#!/usr/bin/env python3
"""
 * This file is part of Gluesync Scheduler Module.
 *
 * Gluesync Scheduler Module is dual-licensed under the following licenses:
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
import pytest
import requests
import subprocess
import sys
import os
from datetime import datetime

# Add the project root directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from models import TaskType

# Test configuration
TEST_DB_URL = "sqlite:///./tests/data/test_api_scheduler.db"
TEST_API_URL = "http://localhost:1718/api"
TEST_PIPELINE_ID = "test-pipeline-api-123"
TEST_ENTITY_ID = "test-entity-api-456"


@pytest.fixture(scope="session")
def setup_api_test_env():
    """Set up test environment with a clean database and running server"""
    # Create test database directory if it doesn't exist
    os.makedirs("./tests/data", exist_ok=True)
    
    # Start the API server in a subprocess
    env = os.environ.copy()
    env["DB_URL"] = TEST_DB_URL
    env["DEBUG"] = "True"
    env["PORT"] = "1718"
    
    # Start the server
    server_process = subprocess.Popen(
        ["python", "app.py"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    # Wait for server to start
    time.sleep(5)
    
    # Check if server is running
    try:
        response = requests.get(f"{TEST_API_URL}/jobs")
        assert response.status_code == 200
    except Exception as e:
        # If server didn't start, print output and raise
        stdout, stderr = server_process.communicate(timeout=1)
        print(f"Server stdout: {stdout.decode()}")
        print(f"Server stderr: {stderr.decode()}")
        server_process.terminate()
        raise Exception(f"Failed to start test server: {e}")
    
    yield
    
    # Clean up
    server_process.terminate()
    server_process.wait()
    
    # Remove test database
    if os.path.exists("./tests/data/test_api_scheduler.db"):
        os.remove("./tests/data/test_api_scheduler.db")


def test_create_job_api(setup_api_test_env):
    """Test creating a job through the API"""
    # Create a job
    job_data = {
        "name": "API Test Job 1",
        "description": "Test job for API testing",
        "task_type": "PIPELINE_START",
        "cron_expression": "*/10 * * * *",  # Run every 10 minutes
        "pipeline_id": TEST_PIPELINE_ID,
        "with_snapshot": False,
        "enabled": True
    }
    
    response = requests.post(f"{TEST_API_URL}/jobs", json=job_data)
    assert response.status_code == 201
    
    job = response.json()
    assert job["name"] == job_data["name"]
    assert job["description"] == job_data["description"]
    assert job["task_type"] == job_data["task_type"]
    assert job["cron_expression"] == job_data["cron_expression"]
    assert job["pipeline_id"] == job_data["pipeline_id"]
    assert job["with_snapshot"] == job_data["with_snapshot"]
    assert job["enabled"] == job_data["enabled"]
    assert "id" in job
    assert "cron_job_identifier" in job
    assert "command" in job
    assert "next_run" in job
    
    # Clean up
    job_id = job["id"]
    requests.delete(f"{TEST_API_URL}/jobs/{job_id}")


def test_get_job_api(setup_api_test_env):
    """Test retrieving a job through the API"""
    # Create a job
    job_data = {
        "name": "API Test Job 2",
        "description": "Test job for API get testing",
        "task_type": "ENTITY_START",
        "cron_expression": "0 */2 * * *",  # Run every 2 hours
        "pipeline_id": TEST_PIPELINE_ID,
        "entity_id": TEST_ENTITY_ID,
        "with_snapshot": True,
        "enabled": True
    }
    
    response = requests.post(f"{TEST_API_URL}/jobs", json=job_data)
    assert response.status_code == 201
    job_id = response.json()["id"]
    
    # Get the job
    response = requests.get(f"{TEST_API_URL}/jobs/{job_id}")
    assert response.status_code == 200
    
    job = response.json()
    assert job["id"] == job_id
    assert job["name"] == job_data["name"]
    assert job["description"] == job_data["description"]
    assert job["task_type"] == job_data["task_type"]
    assert job["cron_expression"] == job_data["cron_expression"]
    assert job["pipeline_id"] == job_data["pipeline_id"]
    assert job["entity_id"] == job_data["entity_id"]
    assert job["with_snapshot"] == job_data["with_snapshot"]
    assert job["enabled"] == job_data["enabled"]
    
    # Clean up
    requests.delete(f"{TEST_API_URL}/jobs/{job_id}")


def test_list_jobs_api(setup_api_test_env):
    """Test listing jobs through the API"""
    # Create multiple jobs
    job_data_1 = {
        "name": "API Test Job 3",
        "description": "Test job for API list testing 1",
        "task_type": "PIPELINE_START",
        "cron_expression": "*/15 * * * *",
        "pipeline_id": TEST_PIPELINE_ID,
        "with_snapshot": False,
        "enabled": True
    }
    
    job_data_2 = {
        "name": "API Test Job 4",
        "description": "Test job for API list testing 2",
        "task_type": "ENTITY_STOP",
        "cron_expression": "0 0 * * *",
        "pipeline_id": TEST_PIPELINE_ID,
        "entity_id": TEST_ENTITY_ID,
        "with_snapshot": False,
        "enabled": False
    }
    
    response_1 = requests.post(f"{TEST_API_URL}/jobs", json=job_data_1)
    assert response_1.status_code == 201
    job_id_1 = response_1.json()["id"]
    
    response_2 = requests.post(f"{TEST_API_URL}/jobs", json=job_data_2)
    assert response_2.status_code == 201
    job_id_2 = response_2.json()["id"]
    
    # List all jobs
    response = requests.get(f"{TEST_API_URL}/jobs")
    assert response.status_code == 200
    
    jobs = response.json()
    assert "items" in jobs
    assert "total" in jobs
    assert jobs["total"] >= 2
    
    # Find our test jobs in the list
    job_ids = [job["id"] for job in jobs["items"]]
    assert job_id_1 in job_ids
    assert job_id_2 in job_ids
    
    # Test filtering by task_type
    response = requests.get(f"{TEST_API_URL}/jobs?task_type=ENTITY_STOP")
    assert response.status_code == 200
    
    jobs = response.json()
    assert any(job["id"] == job_id_2 for job in jobs["items"])
    assert not any(job["id"] == job_id_1 for job in jobs["items"])
    
    # Test filtering by enabled status
    response = requests.get(f"{TEST_API_URL}/jobs?enabled=false")
    assert response.status_code == 200
    
    jobs = response.json()
    assert any(job["id"] == job_id_2 for job in jobs["items"])
    assert not any(job["id"] == job_id_1 for job in jobs["items"])
    
    # Clean up
    requests.delete(f"{TEST_API_URL}/jobs/{job_id_1}")
    requests.delete(f"{TEST_API_URL}/jobs/{job_id_2}")


def test_update_job_api(setup_api_test_env):
    """Test updating a job through the API"""
    # Create a job
    job_data = {
        "name": "API Test Job 5",
        "description": "Test job for API update testing",
        "task_type": "PIPELINE_SNAPSHOT",
        "cron_expression": "0 12 * * *",  # Run at noon
        "pipeline_id": TEST_PIPELINE_ID,
        "with_snapshot": False,
        "enabled": True
    }
    
    response = requests.post(f"{TEST_API_URL}/jobs", json=job_data)
    assert response.status_code == 201
    job_id = response.json()["id"]
    
    # Update the job
    update_data = {
        "name": "Updated API Test Job 5",
        "description": "Updated description",
        "cron_expression": "0 6,18 * * *",  # Run at 6am and 6pm
        "enabled": False
    }
    
    response = requests.put(f"{TEST_API_URL}/jobs/{job_id}", json=update_data)
    assert response.status_code == 200
    
    updated_job = response.json()
    assert updated_job["id"] == job_id
    assert updated_job["name"] == update_data["name"]
    assert updated_job["description"] == update_data["description"]
    assert updated_job["cron_expression"] == update_data["cron_expression"]
    assert updated_job["enabled"] == update_data["enabled"]
    
    # Verify fields not in update_data remain unchanged
    assert updated_job["task_type"] == job_data["task_type"]
    assert updated_job["pipeline_id"] == job_data["pipeline_id"]
    assert updated_job["with_snapshot"] == job_data["with_snapshot"]
    
    # Clean up
    requests.delete(f"{TEST_API_URL}/jobs/{job_id}")


def test_delete_job_api(setup_api_test_env):
    """Test deleting a job through the API"""
    # Create a job
    job_data = {
        "name": "API Test Job 6",
        "description": "Test job for API delete testing",
        "task_type": "ENTITY_SNAPSHOT",
        "cron_expression": "0 0 * * 0",  # Run at midnight on Sundays
        "pipeline_id": TEST_PIPELINE_ID,
        "entity_id": TEST_ENTITY_ID,
        "with_snapshot": False,
        "enabled": True
    }
    
    response = requests.post(f"{TEST_API_URL}/jobs", json=job_data)
    assert response.status_code == 201
    job_id = response.json()["id"]
    
    # Delete the job
    response = requests.delete(f"{TEST_API_URL}/jobs/{job_id}")
    assert response.status_code == 204
    
    # Verify job is deleted
    response = requests.get(f"{TEST_API_URL}/jobs/{job_id}")
    assert response.status_code == 404


def test_invalid_job_creation(setup_api_test_env):
    """Test API validation for invalid job creation"""
    # Test missing required fields
    job_data = {
        "name": "Invalid Job",
        # Missing task_type
        "cron_expression": "0 0 * * *",
        # Missing pipeline_id
        "enabled": True
    }
    
    response = requests.post(f"{TEST_API_URL}/jobs", json=job_data)
    assert response.status_code == 422  # Unprocessable Entity
    
    # Test invalid cron expression
    job_data = {
        "name": "Invalid Job",
        "task_type": "PIPELINE_START",
        "cron_expression": "invalid cron",
        "pipeline_id": TEST_PIPELINE_ID,
        "enabled": True
    }
    
    response = requests.post(f"{TEST_API_URL}/jobs", json=job_data)
    assert response.status_code == 400  # Bad Request
    
    # Test missing entity_id for entity operations
    job_data = {
        "name": "Invalid Job",
        "task_type": "ENTITY_START",
        "cron_expression": "0 0 * * *",
        "pipeline_id": TEST_PIPELINE_ID,
        # Missing entity_id
        "enabled": True
    }
    
    response = requests.post(f"{TEST_API_URL}/jobs", json=job_data)
    assert response.status_code == 422  # Unprocessable Entity


if __name__ == "__main__":
    pytest.main(["-xvs", __file__])
