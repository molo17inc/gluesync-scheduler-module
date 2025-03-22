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

"""
Mock implementation of the croniter library for testing.
This allows tests to run without requiring the actual croniter library to be installed.
"""

import logging
from datetime import datetime, timedelta

# Create a logger for the mock croniter
logger = logging.getLogger("mock_croniter")

class croniter:
    """Mock implementation of the croniter class"""
    
    def __init__(self, cron_expression, start_time=None):
        self.cron_expression = cron_expression
        self.start_time = start_time or datetime.now()
        logger.info(f"Mock croniter created with expression: {cron_expression}")
        
        # Validate the cron expression format
        parts = cron_expression.split()
        if len(parts) < 5:
            raise ValueError(f"Invalid cron expression: {cron_expression}")
    
    def get_next(self, ret_type=datetime):
        """Get the next execution time"""
        # For testing, just return a time 1 hour in the future
        next_time = self.start_time + timedelta(hours=1)
        logger.info(f"Mock croniter next time: {next_time}")
        return next_time
