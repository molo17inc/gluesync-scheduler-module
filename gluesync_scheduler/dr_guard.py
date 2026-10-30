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
Disaster-recovery guard.

Core Hub disaster recovery runs two sites (active and standby). Core Hub on the
active site replicates Chronos's SQLite database (``<DATA_DIR>/scheduler.db``) and,
on promotion, the standby Core Hub installs its replicated copy into Chronos's data
directory. Chronos must therefore have its database open only while its own Core Hub
is the serving active node, and must announce that it holds it.

The guard is opt-in: when ``GS_DR_CONTROL_URL`` is unset every function here is a
no-op and Chronos behaves exactly as before.

* Startup gate: ``activate()`` blocks until ``GET {GS_DR_CONTROL_URL}/health`` reports
  ``serving`` (older Core Hubs without ``serving``: ``role == "ACTIVE"``). It runs
  before anything opens, creates or migrates the database.
* Heartbeat: while the database is held, ``<DATA_DIR>/.dr-chronos-heartbeat`` is
  touched every second (current epoch millis). Core Hub treats the database as held
  when the file is younger than 5 seconds. It is deleted on an orderly stop.
* Runtime watch: when Core Hub is observed not serving (or unreachable) on two
  consecutive polls, the heartbeat stops, the file is removed and the process ends
  immediately with exit code 75. The abrupt exit is intended: an uncommitted SQLite
  transaction is rolled back, and the restart policy brings Chronos back into the
  startup gate.

Standard library only, so the shell entrypoint can use it before anything else.
Command line: ``python3 -m gluesync_scheduler.dr_guard`` waits until Core Hub is the
serving active node and exits 0.
"""

import atexit
import json
import logging
import os
import ssl
import sys
import threading
import time
import urllib.request
from typing import NamedTuple, Optional

logger = logging.getLogger(__name__)

ENV_CONTROL_URL = "GS_DR_CONTROL_URL"
HEARTBEAT_FILE_NAME = ".dr-chronos-heartbeat"
POLL_INTERVAL_SECONDS = 1.0
HEARTBEAT_INTERVAL_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 2.0
BAD_POLLS_BEFORE_EXIT = 2
EXIT_CODE_NOT_SERVING = 75  # EX_TEMPFAIL: the restart policy retries into the gate


class CoreHubState(NamedTuple):
    """What Core Hub's control port said (or why it could not be asked)."""

    serving: bool
    description: str  # "role STANDBY" or "unreachable: <reason>"
    role: Optional[str] = None
    epoch: Optional[int] = None


def control_url() -> str:
    """The configured Core Hub control URL, or an empty string when the guard is off."""
    return os.getenv(ENV_CONTROL_URL, "").strip().rstrip("/")


def is_enabled() -> bool:
    return bool(control_url())


def heartbeat_path() -> str:
    return os.path.join(os.getenv("DATA_DIR", "./data"), HEARTBEAT_FILE_NAME)


def _say(level: int, message: str) -> None:
    """Log through logging once the application configured it, else to stderr.

    The gate runs before the application sets up its logging handlers, and a
    ``logging.basicConfig`` call from here would stop the application from installing
    its own (rotating file) handler later.
    """
    if logging.getLogger().handlers:
        logger.log(level, message)
    else:
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {logging.getLevelName(level)} - dr_guard - {message}",
              file=sys.stderr, flush=True)


def check_core_hub(url: Optional[str] = None) -> CoreHubState:
    """Ask Core Hub's control port whether it is the serving active node.

    Any failure to get a valid answer counts as not serving. Certificates are not
    verified, like the other Core Hub calls made from the local container network.
    """
    base = (url if url is not None else control_url()).rstrip("/")
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=REQUEST_TIMEOUT_SECONDS, context=context) as response:
            body = json.loads(response.read().decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError("unexpected /health body")
    except Exception as e:  # unreachable, timeout, TLS failure, non-200, invalid JSON
        return CoreHubState(False, f"unreachable: {e}")

    role = body.get("role")
    epoch = body.get("epoch")
    if isinstance(body.get("serving"), bool):
        serving = body["serving"]
    else:
        serving = role == "ACTIVE"  # older Core Hub without the "serving" field
    return CoreHubState(serving, f"role {role}", role, epoch if isinstance(epoch, int) else None)


def wait_until_serving(poll_interval: float = POLL_INTERVAL_SECONDS, sleep=None) -> Optional[CoreHubState]:
    """Block until Core Hub is the serving active node, polling every second.

    Logs once per state change. Returns the serving state, or None when the guard is
    not enabled (nothing is waited for).
    """
    if not is_enabled():
        return None
    sleep = sleep or time.sleep
    last_role = object()  # role None means unreachable; the reason text may vary between polls
    while True:
        state = check_core_hub()
        if state.serving:
            _say(logging.INFO, f"Core Hub is the serving active disaster-recovery node ({state.description})")
            return state
        if state.role != last_role:
            _say(logging.INFO, "Waiting for Core Hub to be the serving active disaster-recovery node "
                               f"({state.description})")
            last_role = state.role
        sleep(poll_interval)


class _Heartbeat(threading.Thread):
    """Touches the heartbeat file every second until stopped."""

    def __init__(self, path: str, interval: float = HEARTBEAT_INTERVAL_SECONDS):
        super().__init__(name="dr-heartbeat", daemon=True)
        self.path = path
        self.interval = interval
        self._stop_event = threading.Event()
        self._warned = False

    def beat(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w") as heartbeat_file:
                heartbeat_file.write(str(int(time.time() * 1000)))
            self._warned = False
        except OSError as e:
            if not self._warned:
                _say(logging.WARNING, f"Could not write the disaster-recovery heartbeat {self.path}: {e}")
                self._warned = True

    def run(self) -> None:
        while not self._stop_event.wait(self.interval):
            self.beat()

    def stop(self) -> None:
        """Stop beating and remove the file (so Core Hub sees the database as released)."""
        self._stop_event.set()
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass
        except OSError as e:
            _say(logging.WARNING, f"Could not remove the disaster-recovery heartbeat {self.path}: {e}")


class _Watcher(threading.Thread):
    """Ends the process when Core Hub stops being the serving active node."""

    def __init__(self, heartbeat: _Heartbeat, interval: float = POLL_INTERVAL_SECONDS):
        super().__init__(name="dr-watch", daemon=True)
        self.heartbeat = heartbeat
        self.interval = interval
        self._stop_event = threading.Event()

    def run(self) -> None:
        bad_polls = 0
        while not self._stop_event.wait(self.interval):
            state = check_core_hub()
            bad_polls = 0 if state.serving else bad_polls + 1
            if bad_polls >= BAD_POLLS_BEFORE_EXIT:
                self._give_up(state)
                return

    def stop(self) -> None:
        self._stop_event.set()

    def _give_up(self, state: CoreHubState) -> None:
        self.heartbeat.stop()
        _say(logging.WARNING, f"Core Hub is no longer the serving active node ({state.description}): "
                              "Chronos stops so that disaster recovery can take over its database")
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:
                pass
        # Abrupt on purpose: an uncommitted SQLite transaction is rolled back
        os._exit(EXIT_CODE_NOT_SERVING)


_lock = threading.Lock()
_heartbeat: Optional[_Heartbeat] = None
_watcher: Optional[_Watcher] = None


def activate(wait: bool = True) -> bool:
    """Gate the process on Core Hub being the serving active node, then guard its database.

    Call before anything opens, creates or migrates the database. Idempotent, and a
    no-op (returns False) when ``GS_DR_CONTROL_URL`` is unset. With ``wait`` the call
    blocks until Core Hub is serving; without it the process exits with code 75
    straight away when it is not (for short-lived processes such as a cron-launched
    job runner). Once the gate passed the heartbeat starts (first beat written before
    returning) together with the runtime watch.
    """
    global _heartbeat, _watcher
    if not is_enabled():
        return False
    with _lock:
        if _heartbeat is not None:
            return True
        if wait:
            wait_until_serving()
        else:
            state = check_core_hub()
            if not state.serving:
                _say(logging.WARNING, f"Core Hub is not the serving active disaster-recovery node ({state.description}): "
                                      "not opening the Chronos database")
                sys.exit(EXIT_CODE_NOT_SERVING)
        _heartbeat = _Heartbeat(heartbeat_path())
        _heartbeat.beat()
        _heartbeat.start()
        _watcher = _Watcher(_heartbeat)
        _watcher.start()
        atexit.register(deactivate)
    return True


def deactivate() -> None:
    """Orderly stop: end the watch and the heartbeat and delete the heartbeat file."""
    global _heartbeat, _watcher
    with _lock:
        watcher, heartbeat = _watcher, _heartbeat
        _watcher = _heartbeat = None
    if watcher is not None:
        watcher.stop()
    if heartbeat is not None:
        heartbeat.stop()


def main() -> int:
    """Shell entry point: wait until Core Hub is the serving active node, then exit 0."""
    wait_until_serving()
    return 0


if __name__ == "__main__":
    sys.exit(main())
