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
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker

from gluesync_scheduler.config.settings import settings

# Ensure data directory exists if using SQLite
if settings.DB_URL.startswith('sqlite:'):
    db_path = settings.DB_URL.replace('sqlite:///', '')
    # Handle Windows paths
    if os.name == 'nt' and ':' in db_path:
        # Windows absolute path like C:/app/data/scheduler.db
        db_dir = os.path.dirname(db_path)
    else:
        # Unix path or relative path
        db_dir = os.path.dirname(os.path.abspath(db_path))
    
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
        print(f"Created database directory: {db_dir}")

# Create SQLAlchemy engine with appropriate connection args
if settings.DB_URL.startswith('sqlite:'):
    # SQLite-specific connection arguments
    engine = create_engine(settings.DB_URL, connect_args={"check_same_thread": False})
else:
    # For other database types, don't use SQLite-specific args
    engine = create_engine(settings.DB_URL)

# Create SessionLocal class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create Base class
Base = declarative_base()

# Database dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
