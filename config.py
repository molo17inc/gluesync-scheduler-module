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

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
env_path = Path('.') / '.env'
load_dotenv(dotenv_path=env_path)

class Settings:
    # Core Hub settings (from play_pause.py)
    CORE_HUB_URL = os.getenv('CORE_HUB_URL', 'http://localhost:1717')
    DEFAULT_USER = os.getenv('DEFAULT_USER', 'admin')
    DEFAULT_PASSWORD = os.getenv('DEFAULT_PASSWORD', 'admin')
    ENTITY_START_TIMEOUT = int(os.getenv('ENTITY_START_TIMEOUT', '2'))
    
    # API Server settings
    HOST = os.getenv('HOST', '0.0.0.0')
    PORT = int(os.getenv('PORT', '1717'))
    DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 'yes')
    
    # Database settings
    DB_URL = os.getenv('DB_URL', 'sqlite:///./scheduler.db')
    
    # CORS settings
    ALLOWED_ORIGINS = os.getenv('ALLOWED_ORIGINS', '*').split(',')
    
    # Scheduler settings
    CRONTAB_USER = os.getenv('CRONTAB_USER', None)  # None means current user


settings = Settings()
