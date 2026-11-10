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
Container health probe for Chronos (Docker HEALTHCHECK).

Calls ``GET /health`` on the local server and exits 0 when it answers HTTP 200, 1 in
every other case (no answer, timeout, TLS failure, 503, ...). Standard library only.

The server is reached with the same settings ``run_scheduler.py`` uses to start:

* ``HOST`` / ``PORT``: the bind address (``0.0.0.0`` and ``::`` are probed through
  loopback; ``PORT`` defaults to 8000 like the server).
* ``SSL_ENABLED``: when false the server speaks plain HTTP, so the probe does too.
  When true the server serves HTTPS only if it finds a certificate and key (the
  ``SSL_CERT_FILE`` / ``SSL_KEY_FILE`` pair, or the PKCS12 file from the security
  config); otherwise it silently falls back to HTTP. The probe cannot see that
  decision from the outside, so it tries HTTPS first and, only if the TLS handshake
  is refused, retries over plain HTTP. A timeout is never retried: a server that
  does not answer is unhealthy whatever the scheme.

The certificate is not verified: the probe always talks to the local process.

Disaster recovery: with ``GS_DR_CONTROL_URL`` set, a standby Chronos waits in the
startup gate (``gluesync_scheduler/dr_guard.py``) and has no HTTP server yet. While it
waits the guard touches ``<DATA_DIR>/.dr-chronos-standby`` on every poll, so when the
local probe fails but that marker is younger than 15 seconds the probe reports healthy
(``DATA_DIR`` defaults to the image's ``/app/data``). A stale or missing marker, or no
``GS_DR_CONTROL_URL``, leaves the failure as it is. This file must not import
``gluesync_scheduler``: it uses the marker name and the path rules by convention.

Usage: ``python3 healthcheck.py [--timeout SECONDS]`` (default 5, or the
``HEALTHCHECK_TIMEOUT`` environment variable).
"""

import argparse
import http.client
import os
import socket
import ssl
import sys
import time

HEALTH_PATH = "/health"
DEFAULT_TIMEOUT = 5.0
DEFAULT_PORT = 8000  # same default as run_scheduler.py
DEFAULT_DATA_DIR = "/app/data"  # DATA_DIR of the Linux images
STANDBY_FILE_NAME = ".dr-chronos-standby"  # written by dr_guard while the startup gate is closed
STANDBY_MAX_AGE_SECONDS = 15.0


def _truthy(value):
    return (value or "").strip().lower() in ("true", "1", "t")


def _probe_host():
    host = (os.getenv("HOST") or "").strip()
    if host in ("", "0.0.0.0"):
        return "127.0.0.1"
    if host == "::":
        return "::1"
    return host


def _request(use_ssl, host, port, timeout):
    """Return the HTTP status of GET /health. Redirects are not followed."""
    if use_ssl:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=context)
    else:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", HEALTH_PATH, headers={"Accept": "application/json"})
        response = conn.getresponse()
        response.read()
        return response.status
    finally:
        conn.close()


def probe(timeout=DEFAULT_TIMEOUT):
    """Return True when the local /health endpoint answers 200."""
    host = _probe_host()
    try:
        port = int(os.getenv("PORT") or DEFAULT_PORT)
    except ValueError:
        print(f"healthcheck: invalid PORT {os.getenv('PORT')!r}", file=sys.stderr)
        return False

    schemes = [True, False] if _truthy(os.getenv("SSL_ENABLED")) else [False]
    deadline = time.monotonic() + timeout
    status = None

    for use_ssl in schemes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        scheme = "https" if use_ssl else "http"
        try:
            status = _request(use_ssl, host, port, remaining)
        except (TimeoutError, socket.timeout):
            print(f"healthcheck: {scheme}://{host}:{port}{HEALTH_PATH} timed out", file=sys.stderr)
            return False
        except (ssl.SSLError, ConnectionResetError, http.client.BadStatusLine) as exc:
            # The server answered something that is not TLS (or not HTTP): try the other scheme
            print(f"healthcheck: {scheme}://{host}:{port}{HEALTH_PATH} {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        except (OSError, http.client.HTTPException) as exc:
            print(f"healthcheck: {scheme}://{host}:{port}{HEALTH_PATH} {type(exc).__name__}: {exc}", file=sys.stderr)
            return False
        if status != 200:
            print(f"healthcheck: {scheme}://{host}:{port}{HEALTH_PATH} returned HTTP {status}", file=sys.stderr)
        return status == 200

    return False


def standby_waiting():
    """True when a disaster-recovery standby Chronos is waiting in the startup gate.

    That is: ``GS_DR_CONTROL_URL`` is set and the guard touched its standby marker in
    the last 15 seconds.
    """
    if not (os.getenv("GS_DR_CONTROL_URL") or "").strip():
        return False
    marker = os.path.join(os.getenv("DATA_DIR") or DEFAULT_DATA_DIR, STANDBY_FILE_NAME)
    try:
        age = time.time() - os.stat(marker).st_mtime
    except OSError:
        return False
    return age < STANDBY_MAX_AGE_SECONDS


def main(argv=None):
    parser = argparse.ArgumentParser(description="Chronos container health probe")
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("HEALTHCHECK_TIMEOUT") or DEFAULT_TIMEOUT),
        help="overall timeout in seconds (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    if probe(args.timeout):
        return 0
    if standby_waiting():
        print("healthcheck: standby: waiting for Core Hub to serve", file=sys.stderr)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
