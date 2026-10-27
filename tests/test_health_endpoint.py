#!/usr/bin/env python3
"""Tests for the unauthenticated GET /health liveness endpoint."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gluesync_scheduler.core import health as health_module
from gluesync_scheduler.version import __version__


def _scheduler_service(running=True, jobs=3):
    service = MagicMock()
    service.scheduler.running = running
    service.scheduler.get_jobs.return_value = [object()] * jobs
    return service


def _sdk_client(connected=True):
    sdk = MagicMock()
    sdk.client.is_connected = connected
    return sdk


@pytest.fixture
def client():
    from gluesync_scheduler.core.app import app

    # No Authorization header anywhere in this module: the endpoint is public
    return TestClient(app, raise_server_exceptions=False)


def _get_health(client, scheduler_service, sdk_client):
    with patch.object(health_module, "scheduler_service", scheduler_service), \
            patch.object(health_module, "gluesync_sdk_client", sdk_client):
        return client.get("/health")


def test_health_ok_when_scheduler_running_and_corehub_connected(client):
    response = _get_health(client, _scheduler_service(running=True, jobs=3), _sdk_client(connected=True))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert isinstance(body["uptime_seconds"], int) and body["uptime_seconds"] >= 0
    assert body["scheduler"] == {"running": True, "jobs": 3}
    assert body["corehub"] == {"connected": True}


def test_health_degraded_but_200_when_corehub_disconnected(client):
    response = _get_health(client, _scheduler_service(running=True, jobs=0), _sdk_client(connected=False))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["scheduler"]["running"] is True
    assert body["corehub"]["connected"] is False


def test_health_degraded_when_sdk_client_not_created_yet(client):
    sdk = MagicMock()
    sdk.client = None

    response = _get_health(client, _scheduler_service(), sdk)

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["corehub"]["connected"] is False


def test_health_503_down_when_scheduler_stopped(client):
    response = _get_health(client, _scheduler_service(running=False), _sdk_client(connected=True))

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "down"
    assert body["scheduler"] == {"running": False, "jobs": 0}


def test_health_503_down_even_when_corehub_disconnected(client):
    response = _get_health(client, _scheduler_service(running=False), _sdk_client(connected=False))

    assert response.status_code == 503
    assert response.json()["status"] == "down"


def test_health_survives_errors_reading_state(client):
    scheduler_service = MagicMock()
    scheduler_service.scheduler.running = True
    scheduler_service.scheduler.get_jobs.side_effect = RuntimeError("boom")
    sdk = MagicMock()
    type(sdk).client = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

    response = _get_health(client, scheduler_service, sdk)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["scheduler"] == {"running": True, "jobs": None}


def test_health_needs_no_credentials(client):
    response = _get_health(client, _scheduler_service(), _sdk_client())

    assert response.status_code == 200
    # Same app, an authenticated API route still refuses anonymous callers
    assert client.get("/api/jobs/").status_code in (401, 403)


def test_health_is_not_exposed_under_api_prefix(client):
    response = _get_health(client, _scheduler_service(), _sdk_client())
    assert response.status_code == 200

    assert client.get("/api/health").status_code in (401, 403, 404)
    assert "/health" not in client.get("/api/openapi.json").json()["paths"]
