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

"""Authentication of Chronos's calls to its own internal /api/pipelines/* API.

Only Chronos calls those routes (the scheduler, chained events, the CLI job runner), so
every call carries a per-installation token that nobody else knows. The token comes from
CHRONOS_INTERNAL_TOKEN when set (several replicas, or SCHEDULER_INTERNAL_HOST pointing at
another container), otherwise from <DATA_DIR>/.internal-token, created on first use with
mode 0600.
"""

import logging
import os
import secrets
import threading
from typing import Dict, Optional

logger = logging.getLogger(__name__)

INTERNAL_TOKEN_HEADER = "X-Chronos-Internal-Token"
_TOKEN_FILE_NAME = ".internal-token"

_lock = threading.Lock()
_cached: Optional[str] = None


def _token_file() -> str:
    return os.path.join(os.getenv("DATA_DIR", "./data"), _TOKEN_FILE_NAME)


def _read_or_create(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            existing = f.read().strip()
        if existing:
            return existing
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    token = secrets.token_urlsafe(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another process created it first: use theirs.
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token)
    logger.info("Created the internal API token file %s", path)
    return token


def internal_token() -> str:
    """The token that authenticates calls to the internal pipeline API."""
    global _cached
    configured = os.getenv("CHRONOS_INTERNAL_TOKEN", "").strip()
    if configured:
        return configured
    with _lock:
        if _cached is None:
            try:
                _cached = _read_or_create(_token_file())
            except OSError as exc:
                # Read-only or missing data directory: calls from this process still work,
                # a separate CLI job runner then needs CHRONOS_INTERNAL_TOKEN.
                logger.warning("Cannot store the internal API token in %s (%s): using a per-process token", _token_file(), exc)
                _cached = secrets.token_urlsafe(32)
        return _cached


def internal_headers() -> Dict[str, str]:
    """Headers for a call to the internal pipeline API."""
    return {"Content-Type": "application/json", INTERNAL_TOKEN_HEADER: internal_token()}


def is_internal_token(provided: Optional[str]) -> bool:
    if not provided:
        return False
    return secrets.compare_digest(provided.strip(), internal_token())


def reset_cache() -> None:
    """Forget the cached token (tests)."""
    global _cached
    with _lock:
        _cached = None
