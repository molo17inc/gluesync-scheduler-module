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
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
env_path = Path('.') / '.env'
load_dotenv(dotenv_path=env_path)

class Settings:
    # Core Hub settings
    # CORE_HUB_URL will be dynamically set from the SDK after discovery
    # If not provided, UDP discovery will be used to find the CoreHub
    CORE_HUB_URL = os.getenv('CORE_HUB_URL', '')
    ENTITY_START_TIMEOUT = int(os.getenv('ENTITY_START_TIMEOUT', '2'))
    
    def update_corehub_url(self, url: str):
        """Update the CoreHub URL from the SDK"""
        if url:
            self.CORE_HUB_URL = url
    
    # Web server settings
    HOST = os.getenv('HOST', '0.0.0.0')
    PORT = int(os.getenv('PORT', '8000'))
    
    # Database settings
    DATA_DIR = os.getenv('DATA_DIR', './data')
    DB_URL = os.getenv('DB_URL', f'sqlite:///{DATA_DIR}/scheduler.db')
    
    # Timezone settings
    TIMEZONE = os.getenv('TIMEZONE', 'Europe/Rome')
    
    # SSL settings
    SSL_ENABLED = os.getenv('SSL_ENABLED', 'False').lower() in ('true', '1', 't')
    
    # Gluesync SDK settings
    GLUESYNC_LICENSE_FILE = os.getenv('GLUESYNC_LICENSE_FILE', '/opt/gluesync/data/gs-license.dat')
    GLUESYNC_MODULE_TAG = os.getenv('GLUESYNC_MODULE_TAG', 'scheduler-module')
    GLUESYNC_USE_SSL = os.getenv('GLUESYNC_USE_SSL', 'False').lower() in ('true', '1', 't')
    GLUESYNC_SECURITY_CONFIG = os.getenv('GLUESYNC_SECURITY_CONFIG', '/opt/gluesync/data/security-config.json')
    
    # Logging settings
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_DIR = os.getenv('LOG_DIR', './logs')
    
    # Cron settings
    CRONTAB_USER = os.getenv('CRONTAB_USER', '')
    CRON_LOG_DIR = os.getenv('CRON_LOG_DIR', './logs')


settings = Settings()
