#!/usr/bin/env python3
"""Core Hub resilience: 502/503 retried (no-retry-on-corehub-5xx), a client timeout with an honest
message (slow-corehub-false-failure), the refusal reason on the job error (maintenance-406-reason-lost)
and the 401 re-login on the SDK's own loop (sdk-relogin-on-throwaway-event-loop)."""

import asyncio
import os
import sys
import threading
import time

import pytest
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gluesync_scheduler.core import play_pause
from gluesync_scheduler.core.play_pause import CoreHubClient


class _Resp:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.text = str(self._body)
        self.headers = {"content-type": "application/json"}
        self.content = self.text.encode()

    def json(self):
        return self._body


@pytest.fixture
def hub(monkeypatch):
    client = object.__new__(CoreHubClient)
    client.base_url = "http://corehub.test"
    client.token = "jwt"
    client._initialized = True
    monkeypatch.setattr(client, "_get_current_token", lambda: ("jwt", True))
    monkeypatch.setattr(client, "_get_current_corehub_url", lambda: "http://corehub.test")
    monkeypatch.setattr(play_pause.time, "sleep", lambda s: None)
    queue, calls = [], []

    def fake_request(method, url, headers, body, params, verify, timeout=None):
        calls.append({"method": method, "url": url, "timeout": timeout})
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(CoreHubClient, "_do_request", staticmethod(fake_request))
    return client, queue, calls


def test_a_503_is_sent_again_and_the_command_succeeds(hub):
    client, queue, calls = hub
    queue += [_Resp(503, {"message": "busy"}), _Resp(200, {})]
    assert client.stop_pipeline("pl-1") is True
    assert len(calls) == 2


def test_a_persistent_503_fails_after_the_retries(hub, monkeypatch):
    monkeypatch.setenv("CHRONOS_COREHUB_RETRIES", "2")
    client, queue, calls = hub
    queue += [_Resp(503, {"message": "busy"})] * 3
    assert client.stop_pipeline("pl-1") is False
    assert len(calls) == 3


@pytest.mark.parametrize("status", [400, 406, 500, 504])
def test_other_errors_are_not_sent_again(hub, status):
    client, queue, calls = hub
    queue += [_Resp(status, {"message": "no"})]
    assert client.stop_pipeline("pl-1") is False
    assert len(calls) == 1


def test_raw_callers_keep_their_own_retry_policy(hub):
    client, queue, calls = hub
    queue += [_Resp(503, {"message": "busy"})]
    client.fetch_core_hub("/pipelines/pl-1/transactions-audit/start", method="POST", raw=True)
    assert len(calls) == 1


def test_requests_get_a_default_timeout(hub, monkeypatch):
    monkeypatch.setenv("CHRONOS_COREHUB_TIMEOUT", "42")
    client, queue, calls = hub
    queue += [_Resp(200, {})]
    client.stop_pipeline("pl-1")
    assert calls[0]["timeout"] == 42


def test_a_timeout_says_the_command_may_still_be_applied(hub):
    client, queue, _ = hub
    queue += [requests.exceptions.ReadTimeout("slow")]
    result = client.fetch_core_hub("/pipelines/pl-1/commands/sync/stop", method="POST")
    assert result["status_code"] == 504
    assert "timed out" in result["message"]
    assert "may still be applied" in result["message"]


def test_the_internal_call_waits_longer_than_core_hub(monkeypatch):
    from gluesync_scheduler.core.internal_auth import internal_http_timeout_seconds
    monkeypatch.delenv("SCHEDULER_INTERNAL_HTTP_TIMEOUT", raising=False)
    monkeypatch.delenv("CHRONOS_COREHUB_TIMEOUT", raising=False)
    assert internal_http_timeout_seconds() > play_pause.corehub_timeout_seconds()


def test_the_refusal_reason_reaches_the_job_error(hub):
    from gluesync_scheduler.services.job_service import _with_core_hub_reason
    client, queue, _ = hub
    since = time.time()
    reason = "Pipeline with id pl-9 is in maintenance. Please wait until the maintenance mode is disabled."
    queue += [_Resp(406, {"message": reason})]
    assert client.start_pipeline("pl-9") is False
    message = _with_core_hub_reason("Failed to start pipeline pl-9", "pl-9", since)
    assert message == f"Failed to start pipeline pl-9 (Core Hub answered 406: {reason})"
    # Not added twice, not for another pipeline, not from before the run.
    assert _with_core_hub_reason(message, "pl-9", since) == message
    assert _with_core_hub_reason("Failed", "pl-other", since) == "Failed"
    assert _with_core_hub_reason("Failed", "pl-9", time.time() + 5) == "Failed"


def test_relogin_from_a_worker_thread_runs_on_the_sdk_loop(monkeypatch):
    sdk = play_pause.gluesync_sdk_client
    loop = asyncio.new_event_loop()
    runner = threading.Thread(target=loop.run_forever, daemon=True)
    runner.start()
    seen = []

    async def fake_initialize(self, force_reconnect=False):
        seen.append((asyncio.get_running_loop(), force_reconnect))

    # Patch the class, not the singleton: an instance attribute would outlive the test.
    monkeypatch.setattr(type(sdk), "_home_loop", loop, raising=False)
    monkeypatch.setattr(type(sdk), "initialize", fake_initialize)
    try:
        worker = threading.Thread(target=CoreHubClient._reinitialize_sdk_client)
        worker.start()
        worker.join(5)
        assert seen == [(loop, True)]
    finally:
        loop.call_soon_threadsafe(loop.stop)
        runner.join(5)
        loop.close()
