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
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from crontab import CronTab

# Add the project root directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from database import Base
from models import ScheduledJob, TaskType
from services.cron_service import CronService
from services.job_service import JobService

# Test configuration
TEST_DB_URL = "sqlite:///./tests/data/test_scheduler.db"
TEST_API_URL = "http://127.0.0.1:1717/api"  # Use IP address instead of localhost
TEST_PIPELINE_ID = "test-pipeline-123"
TEST_ENTITY_ID = "test-entity-456"


@pytest.fixture(scope="session")
def setup_test_env():
    """Set up test environment with a clean database and running server"""
    # Create test database
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    
    # Start the API server in a subprocess
    env = os.environ.copy()
    env["DB_URL"] = TEST_DB_URL
    env["DEBUG"] = "True"
    env["PORT"] = "1717"
    env["HOST"] = "0.0.0.0"  # Bind to all interfaces, not just localhost
    
    # Make sure the data directory exists
    os.makedirs(os.path.dirname(TEST_DB_URL.replace('sqlite:///', '')), exist_ok=True)
    
    print("Starting test API server...")
    print(f"Using database: {TEST_DB_URL}")
    print(f"Server will bind to {env['HOST']}:{env['PORT']}")
    
    # Start the server
    server_process = subprocess.Popen(
        ["python", "app.py"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    # Wait for server to start - longer in CI environment
    wait_time = 10  # Increased wait time
    print(f"Waiting {wait_time} seconds for server to start...")
    time.sleep(wait_time)
    
    # Check if server is running
    max_retries = 3
    retry_delay = 2
    success = False
    
    for attempt in range(max_retries):
        try:
            print(f"Attempt {attempt+1}/{max_retries} to connect to server...")
            response = requests.get(f"{TEST_API_URL}/jobs", timeout=5)
            if response.status_code == 200:
                print("Successfully connected to test server!")
                success = True
                break
            else:
                print(f"Server responded with status code {response.status_code}")
        except Exception as e:
            print(f"Connection attempt {attempt+1} failed: {e}")
            if attempt < max_retries - 1:
                print(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
    
    if not success:
        # If server didn't start, print output and raise
        print("All connection attempts failed. Checking server output...")
        stdout, stderr = server_process.communicate(timeout=1)
        print(f"Server stdout: {stdout.decode()}")
        print(f"Server stderr: {stderr.decode()}")
        server_process.terminate()
        raise Exception("Failed to start test server after multiple attempts")
    
    yield
    
    # Clean up
    server_process.terminate()
    server_process.wait()
    
    # Remove test database
    if os.path.exists("./tests/data/test_scheduler.db"):
        os.remove("./tests/data/test_scheduler.db")


@pytest.fixture
def db_session():
    """Create a new database session for a test"""
    engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    
    yield session
    
    # Clean up
    session.close()


@pytest.fixture
def cron_service():
    """Create a CronService instance for testing"""
    service = CronService()
    
    # Store original crontab to restore later
    original_crontab = service.crontab.render()
    
    yield service
    
    # Clean up any test cron jobs
    for job in service.crontab.find_comment("test_"):
        service.crontab.remove(job)
    
    # Write changes back
    service.crontab.write()


def test_create_and_verify_cron_job(setup_test_env, db_session, cron_service):
    """Test creating a cron job through the API and verify it exists in the crontab"""
    # Create a job via API
    job_data = {
        "name": "Test Job 1",
        "description": "Test job for CI",
        "task_type": "PIPELINE_START",
        "cron_expression": "*/5 * * * *",  # Run every 5 minutes
        "pipeline_id": TEST_PIPELINE_ID,
        "with_snapshot": False,
        "enabled": True
    }
    
    response = requests.post(f"{TEST_API_URL}/jobs", json=job_data)
    assert response.status_code == 201
    
    job_id = response.json()["id"]
    cron_job_identifier = response.json()["cron_job_identifier"]
    
    # Verify job exists in database
    job = db_session.query(ScheduledJob).filter(ScheduledJob.id == job_id).first()
    assert job is not None
    assert job.name == job_data["name"]
    assert job.cron_expression == job_data["cron_expression"]
    
    # Verify job exists in crontab
    found = False
    for cron_job in cron_service.crontab.find_comment(cron_job_identifier):
        found = True
        assert cron_job.enabled
        assert cron_job.slices.minute.parts == [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]
    
    assert found, "Cron job not found in crontab"
    
    # Clean up
    requests.delete(f"{TEST_API_URL}/jobs/{job_id}")


def test_update_cron_job(setup_test_env, db_session, cron_service):
    """Test updating a cron job and verify changes are reflected in the crontab"""
    # Create a job via API
    job_data = {
        "name": "Test Job 2",
        "description": "Test job for CI - update test",
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
    cron_job_identifier = response.json()["cron_job_identifier"]
    
    # Update the job
    update_data = {
        "name": "Updated Test Job 2",
        "cron_expression": "0 */4 * * *",  # Change to every 4 hours
        "enabled": False
    }
    
    response = requests.put(f"{TEST_API_URL}/jobs/{job_id}", json=update_data)
    assert response.status_code == 200
    
    # Verify job is updated in database
    job = db_session.query(ScheduledJob).filter(ScheduledJob.id == job_id).first()
    assert job is not None
    assert job.name == update_data["name"]
    assert job.cron_expression == update_data["cron_expression"]
    assert not job.enabled
    
    # Verify job is updated in crontab
    found = False
    for cron_job in cron_service.crontab.find_comment(cron_job_identifier):
        found = True
        assert not cron_job.enabled
        assert cron_job.slices.hour.parts == [0, 4, 8, 12, 16, 20]
    
    assert found, "Cron job not found in crontab"
    
    # Clean up
    requests.delete(f"{TEST_API_URL}/jobs/{job_id}")


def test_delete_cron_job(setup_test_env, db_session, cron_service):
    """Test deleting a cron job and verify it's removed from the crontab"""
    # Create a job via API
    job_data = {
        "name": "Test Job 3",
        "description": "Test job for CI - delete test",
        "task_type": "PIPELINE_STOP",
        "cron_expression": "0 0 * * *",  # Run at midnight
        "pipeline_id": TEST_PIPELINE_ID,
        "enabled": True
    }
    
    response = requests.post(f"{TEST_API_URL}/jobs", json=job_data)
    assert response.status_code == 201
    
    job_id = response.json()["id"]
    cron_job_identifier = response.json()["cron_job_identifier"]
    
    # Verify job exists in crontab
    found = False
    for cron_job in cron_service.crontab.find_comment(cron_job_identifier):
        found = True
    
    assert found, "Cron job not found in crontab"
    
    # Delete the job
    response = requests.delete(f"{TEST_API_URL}/jobs/{job_id}")
    assert response.status_code == 204
    
    # Verify job is deleted from database
    job = db_session.query(ScheduledJob).filter(ScheduledJob.id == job_id).first()
    assert job is None
    
    # Verify job is deleted from crontab
    found = False
    for cron_job in cron_service.crontab.find_comment(cron_job_identifier):
        found = True
    
    assert not found, "Cron job still exists in crontab after deletion"


def test_job_execution(setup_test_env, db_session):
    """Test that a job is executed when its scheduled time arrives"""
    # Create a job that will run in 1 minute
    now = datetime.now()
    one_minute_later = now + timedelta(minutes=1)
    
    cron_expression = f"{one_minute_later.minute} {one_minute_later.hour} * * *"
    
    job_data = {
        "name": "Test Job 4",
        "description": "Test job for CI - execution test",
        "task_type": "ENTITY_SNAPSHOT",
        "cron_expression": cron_expression,
        "pipeline_id": TEST_PIPELINE_ID,
        "entity_id": TEST_ENTITY_ID,
        "enabled": True
    }
    
    response = requests.post(f"{TEST_API_URL}/jobs", json=job_data)
    assert response.status_code == 201
    
    job_id = response.json()["id"]
    
    # Wait for the job to execute (a bit more than 1 minute)
    time.sleep(70)
    
    # Check if the job's last_run has been updated
    response = requests.get(f"{TEST_API_URL}/jobs/{job_id}")
    assert response.status_code == 200
    
    job_data = response.json()
    assert job_data["last_run"] is not None
    
    # Clean up
    requests.delete(f"{TEST_API_URL}/jobs/{job_id}")


if __name__ == "__main__":
    pytest.main(["-xvs", __file__])
