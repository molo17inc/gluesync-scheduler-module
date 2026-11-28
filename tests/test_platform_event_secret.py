#!/usr/bin/env python3
"""The platform-event callback needs the flow's webhook secret (sandbox finding
platform-event-endpoint-unauthenticated)."""

import os
import sys
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gluesync_scheduler.api import webhook_router
from gluesync_scheduler.models.models import Base, TriggerFlow
from gluesync_scheduler.services.chain_execution_service import (
    PLATFORM_WEBHOOK_SECRET_HEADER,
    platform_webhook_secret,
)


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)
    db = session()
    db.add(TriggerFlow(id=7, name="probe", secret_token="tok-7", enabled=True, platform_event="ENTITY_CDC_STARTED"))
    db.commit()
    db.close()
    monkeypatch.setattr("gluesync_scheduler.db.database.SessionLocal", session)
    fired = []

    async def fake_fire(self, flow_id, source=None, event_payload=None):
        fired.append(flow_id)
        return True, None

    monkeypatch.setattr("gluesync_scheduler.services.trigger_flow_service.TriggerFlowService.fire", fake_fire)
    app = FastAPI()
    app.include_router(webhook_router.router, prefix="/api")
    with TestClient(app) as c:
        c.fired = fired
        yield c


def _post(client, flow_id, secret=None):
    headers = {"EXT_MODULE": "chronos", "X-Trigger-Flow-ID": str(flow_id)}
    if secret is not None:
        headers[PLATFORM_WEBHOOK_SECRET_HEADER] = secret
    return client.post("/api/webhooks/platform-event", json={"event": {"type": "ENTITY_CDC_STARTED"}}, headers=headers)


def test_without_the_secret_is_401_and_nothing_fires(client):
    assert _post(client, 7).status_code == 401
    assert client.fired == []


def test_a_wrong_secret_is_401(client):
    assert _post(client, 7, "nope").status_code == 401
    assert _post(client, 7, platform_webhook_secret(7, "other-token")).status_code == 401
    assert client.fired == []


def test_an_unknown_flow_is_401_not_404(client):
    assert _post(client, 99, platform_webhook_secret(99, "tok-7")).status_code == 401


def test_the_right_secret_fires_the_flow(client):
    r = _post(client, 7, platform_webhook_secret(7, "tok-7"))
    assert r.status_code == 200
    assert r.json()["acknowledged"] is True
    for _ in range(50):  # the flow fires in a background task
        if client.fired:
            break
        time.sleep(0.02)
    assert client.fired == [7]


def test_the_secret_depends_on_flow_and_token():
    assert platform_webhook_secret(1, "a") != platform_webhook_secret(2, "a")
    assert platform_webhook_secret(1, "a") != platform_webhook_secret(1, "b")
    assert platform_webhook_secret(1, "tok-7") != "tok-7"
