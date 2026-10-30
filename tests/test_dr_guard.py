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
 * Copyright (C) 2026 MOLO17. All rights reserved.
"""

"""
Disaster-recovery guard: startup gate, heartbeat file and runtime watch.
"""

import json
import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gluesync_scheduler import dr_guard
from gluesync_scheduler.dr_guard import CoreHubState

SERVING = CoreHubState(True, "role ACTIVE", "ACTIVE", 3)
STANDBY = CoreHubState(False, "role STANDBY", "STANDBY", 3)
UNREACHABLE = CoreHubState(False, "unreachable: connection refused")


@pytest.fixture(autouse=True)
def clean_guard(monkeypatch, tmp_path):
    """Every test starts without an active guard, with DATA_DIR in a temp directory."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv(dr_guard.ENV_CONTROL_URL, raising=False)
    dr_guard.deactivate()
    yield
    dr_guard.deactivate()


class _CoreHub:
    """A tiny stand-in for Core Hub's control port."""

    def __init__(self, body, status=200):
        self.body = body
        self.status = status
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(owner.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(owner.body).encode())

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def core_hub():
    hubs = []

    def make(body, status=200):
        hub = _CoreHub(body, status)
        hubs.append(hub)
        return hub

    yield make
    for hub in hubs:
        hub.close()


def _scripted(monkeypatch, states):
    """check_core_hub answers the given states in order, then the last one forever."""
    answers = list(states)
    calls = []

    def fake(url=None):
        calls.append(url)
        return answers.pop(0) if len(answers) > 1 else answers[0]

    monkeypatch.setattr(dr_guard, "check_core_hub", fake)
    return calls


def _wait_for(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class TestCheckCoreHub:
    def test_serving_flag_is_used(self, core_hub):
        hub = core_hub({"status": "UP", "role": "ACTIVE", "epoch": 7, "serving": False})
        state = dr_guard.check_core_hub(hub.url)
        assert state.serving is False
        assert (state.role, state.epoch) == ("ACTIVE", 7)

    def test_legacy_health_without_serving_active_is_serving(self, core_hub):
        hub = core_hub({"status": "UP", "role": "ACTIVE", "epoch": 1})
        assert dr_guard.check_core_hub(hub.url).serving is True

    @pytest.mark.parametrize("role", ["STANDBY", "PROMOTING", "FENCED"])
    def test_legacy_health_without_serving_other_roles_not_serving(self, core_hub, role):
        hub = core_hub({"status": "UP", "role": role, "epoch": 1})
        assert dr_guard.check_core_hub(hub.url).serving is False

    def test_error_status_is_not_serving(self, core_hub):
        hub = core_hub({"status": "UP", "role": "ACTIVE", "serving": True}, status=503)
        state = dr_guard.check_core_hub(hub.url)
        assert state.serving is False
        assert state.description.startswith("unreachable")

    def test_unreachable_is_not_serving(self):
        state = dr_guard.check_core_hub("http://127.0.0.1:1")
        assert state.serving is False
        assert state.description.startswith("unreachable")


class TestGate:
    def test_noop_when_env_unset(self, monkeypatch, tmp_path):
        calls = _scripted(monkeypatch, [STANDBY])
        assert dr_guard.activate() is False
        assert dr_guard.wait_until_serving() is None
        assert calls == []
        assert not (tmp_path / dr_guard.HEARTBEAT_FILE_NAME).exists()
        assert dr_guard._heartbeat is None and dr_guard._watcher is None

    def test_waits_until_serving_and_logs_once_per_state_change(self, monkeypatch, caplog):
        monkeypatch.setenv(dr_guard.ENV_CONTROL_URL, "http://corehub:9999")
        calls = _scripted(monkeypatch, [UNREACHABLE, UNREACHABLE, STANDBY, STANDBY, STANDBY, SERVING])
        sleeps = []
        with caplog.at_level(logging.INFO, logger=dr_guard.logger.name):
            logging.getLogger().addHandler(logging.NullHandler())  # route _say through logging
            try:
                state = dr_guard.wait_until_serving(sleep=sleeps.append)
            finally:
                logging.getLogger().handlers.pop()
        assert state.serving is True
        assert len(calls) == 6
        assert len(sleeps) == 5
        waiting = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Waiting")]
        assert len(waiting) == 2  # unreachable, then STANDBY
        assert waiting[1] == ("Waiting for Core Hub to be the serving active disaster-recovery node "
                              "(role STANDBY)")

    def test_activate_blocks_until_serving_then_starts_heartbeat(self, monkeypatch, tmp_path):
        monkeypatch.setenv(dr_guard.ENV_CONTROL_URL, "http://corehub:9999")
        _scripted(monkeypatch, [STANDBY, SERVING])
        monkeypatch.setattr(time, "sleep", lambda seconds: None)
        heartbeat = tmp_path / dr_guard.HEARTBEAT_FILE_NAME

        assert dr_guard.activate() is True
        assert heartbeat.exists()  # first beat written before activate returns
        assert dr_guard.activate() is True  # idempotent

    def test_not_waiting_exits_when_not_serving(self, monkeypatch, tmp_path):
        monkeypatch.setenv(dr_guard.ENV_CONTROL_URL, "http://corehub:9999")
        _scripted(monkeypatch, [STANDBY])
        with pytest.raises(SystemExit) as exit_info:
            dr_guard.activate(wait=False)
        assert exit_info.value.code == dr_guard.EXIT_CODE_NOT_SERVING
        assert not (tmp_path / dr_guard.HEARTBEAT_FILE_NAME).exists()

    def test_not_waiting_passes_when_serving(self, monkeypatch, tmp_path):
        monkeypatch.setenv(dr_guard.ENV_CONTROL_URL, "http://corehub:9999")
        _scripted(monkeypatch, [SERVING])
        assert dr_guard.activate(wait=False) is True
        assert (tmp_path / dr_guard.HEARTBEAT_FILE_NAME).exists()

    def test_cli_waits_and_exits_zero(self, monkeypatch):
        monkeypatch.setenv(dr_guard.ENV_CONTROL_URL, "http://corehub:9999")
        _scripted(monkeypatch, [STANDBY, SERVING])
        monkeypatch.setattr(time, "sleep", lambda seconds: None)
        assert dr_guard.main() == 0


class TestHeartbeat:
    def test_file_is_written_refreshed_and_removed(self, tmp_path):
        path = tmp_path / dr_guard.HEARTBEAT_FILE_NAME
        heartbeat = dr_guard._Heartbeat(str(path), interval=0.02)
        before = int(time.time() * 1000)
        heartbeat.beat()
        first = int(path.read_text())
        assert before <= first <= int(time.time() * 1000)

        heartbeat.start()
        assert _wait_for(lambda: int(path.read_text() or 0) > first)

        heartbeat.stop()
        assert not path.exists()
        heartbeat.join(timeout=2)
        assert not heartbeat.is_alive()
        time.sleep(0.1)
        assert not path.exists()  # no beat after the stop

    def test_activate_then_deactivate_removes_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv(dr_guard.ENV_CONTROL_URL, "http://corehub:9999")
        _scripted(monkeypatch, [SERVING])
        dr_guard.activate()
        path = tmp_path / dr_guard.HEARTBEAT_FILE_NAME
        assert path.exists()
        dr_guard.deactivate()
        assert not path.exists()


class TestWatcher:
    def _watcher(self, tmp_path):
        heartbeat = dr_guard._Heartbeat(str(tmp_path / dr_guard.HEARTBEAT_FILE_NAME), interval=60)
        heartbeat.beat()
        return heartbeat, dr_guard._Watcher(heartbeat, interval=0.01)

    @pytest.mark.parametrize("bad", [STANDBY, UNREACHABLE])
    def test_exits_after_two_bad_polls(self, monkeypatch, tmp_path, caplog, bad):
        calls = _scripted(monkeypatch, [SERVING, bad])
        exits = []
        done = threading.Event()
        monkeypatch.setattr(os, "_exit", lambda code: (exits.append(code), done.set()))
        heartbeat, watcher = self._watcher(tmp_path)
        logging.getLogger().addHandler(logging.NullHandler())
        try:
            with caplog.at_level(logging.WARNING, logger=dr_guard.logger.name):
                watcher.start()
                assert done.wait(5)
                watcher.join(timeout=2)
        finally:
            logging.getLogger().handlers.pop()

        assert exits == [75]
        assert len(calls) == 3  # one serving poll, then exactly two bad ones
        assert not (tmp_path / dr_guard.HEARTBEAT_FILE_NAME).exists()
        message = next(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
        assert message.startswith("Core Hub is no longer the serving active node (")
        assert message.endswith("Chronos stops so that disaster recovery can take over its database")

    def test_single_bad_poll_does_not_exit(self, monkeypatch, tmp_path):
        _scripted(monkeypatch, [SERVING, STANDBY, SERVING])
        exits = []
        monkeypatch.setattr(os, "_exit", exits.append)
        calls_seen = threading.Event()
        original = dr_guard.check_core_hub
        counter = {"n": 0}

        def counting(url=None):
            counter["n"] += 1
            if counter["n"] >= 6:
                calls_seen.set()
            return original(url)

        monkeypatch.setattr(dr_guard, "check_core_hub", counting)
        heartbeat, watcher = self._watcher(tmp_path)
        watcher.start()
        assert calls_seen.wait(5)
        watcher.stop()
        watcher.join(timeout=2)
        assert exits == []
        assert (tmp_path / dr_guard.HEARTBEAT_FILE_NAME).exists()
