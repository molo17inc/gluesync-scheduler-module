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

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def get_env_timezone(default: str = "UTC") -> str:
    """Return the effective timezone from environment variables.

    Priority order:
    1. TZ
    2. TIMEZONE (deprecated)
    3. Provided default (UTC by default)

    When falling back to TIMEZONE, a deprecation warning is logged.
    """
    tz = os.getenv("TZ")
    if tz:
        return tz

    legacy_tz = os.getenv("TIMEZONE")
    if legacy_tz:
        logger.warning(
            "Environment variable TIMEZONE is deprecated and will be removed in a "
            "future release. Please use TZ instead."
        )
        return legacy_tz

    return default
