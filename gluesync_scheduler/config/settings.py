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

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables from .env file if it exists
env_path = Path('.') / '.env'
load_dotenv(dotenv_path=env_path)

class Settings:
    # Core Hub settings
    # GLUESYNC_HOST will be dynamically set from the SDK after discovery
    # If not provided, UDP discovery will be used to find the CoreHub
    GLUESYNC_HOST = os.getenv('GLUESYNC_HOST', '')
    ENTITY_START_TIMEOUT = int(os.getenv('ENTITY_START_TIMEOUT', '2'))
    
    def update_corehub_url(self, url: str):
        """Update the CoreHub URL from the SDK"""
        if url:
            self.GLUESYNC_HOST = url
    
    # Web server settings
    HOST = os.getenv('HOST', '0.0.0.0')
    PORT = int(os.getenv('PORT', '8000'))
    
    # Database settings
    DATA_DIR = os.getenv('DATA_DIR', './data')
    
    # Ensure we have an absolute path for the database
    if os.getenv('DB_URL'):
        DB_URL = os.getenv('DB_URL')
    else:
        # Convert relative path to absolute path for consistency
        abs_data_dir = os.path.abspath(DATA_DIR)
        DB_URL = f'sqlite:///{abs_data_dir}/scheduler.db'
        logger.info(f"Using absolute database path: {abs_data_dir}/scheduler.db")
    
    # Timezone settings
    TIMEZONE = os.getenv('TIMEZONE', 'Europe/Rome')
    
    # Debug logging for timezone settings
    def __init__(self):
        # Log all environment variables
        logger.info("Environment variables related to timezone:")
        for k, v in os.environ.items():
            if 'TIME' in k.upper() or 'TZ' in k.upper():
                logger.info(f"  {k}={v}")
        logger.info(f"Configured TIMEZONE: {self.TIMEZONE}")
    
    # SSL settings
    SSL_ENABLED = os.getenv('SSL_ENABLED', 'False').lower() in ('true', '1', 't')
    SSL_SKIP_VERIFY = os.getenv('SSL_SKIP_VERIFY', 'False').lower() in ('true', '1', 't')
    
    # Gluesync SDK settings
    GLUESYNC_LICENSE_FILE = os.getenv('GLUESYNC_LICENSE_FILE', '/opt/gluesync/data/gs-license.dat')
    GLUESYNC_MODULE_TAG = os.getenv('GLUESYNC_MODULE_TAG', 'chronos')
    GLUESYNC_SECURITY_CONFIG = os.getenv('GLUESYNC_SECURITY_CONFIG', '/opt/gluesync/data/security-config.json')
    
    # Logging settings
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_DIR = os.getenv('LOG_DIR', './logs')
    DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 't')
    
    # Cron settings
    # Use current user for crontab by default (True), or specific user if provided
    CRONTAB_USER = os.getenv('CRONTAB_USER', '')
    CRON_LOG_DIR = os.getenv('CRON_LOG_DIR', './logs')
    
    # Job execution settings
    # When FIRE_ONCE is True, jobs will be disabled after their first execution
    FIRE_ONCE = os.getenv('FIRE_ONCE', 'False').lower() in ('true', '1', 't')


settings = Settings()

# Log timezone settings
logger.info(f"Actual configured timezone: {settings.TIMEZONE}")
