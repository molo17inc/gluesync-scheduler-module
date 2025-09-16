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
import sys
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from gluesync_scheduler.config.settings import settings

def ensure_sqlite_db_path():
    """Ensure the SQLite database directory exists and return the path."""
    if not settings.DB_URL.startswith('sqlite:'):
        return
        
    # Extract the database path
    db_path = settings.DB_URL.replace('sqlite:///', '')
    
    # Handle Windows paths (C:/path/db.sqlite)
    if ':' in db_path and len(db_path) > 2 and db_path[1] == ':':
        db_path = db_path[2:]  # Remove the leading / from /C:/path
    
    db_path = Path(db_path).absolute()
    db_dir = db_path.parent
    
    # Create directory if it doesn't exist
    try:
        db_dir.mkdir(parents=True, exist_ok=True)
        print(f"[Database] Created database directory: {db_dir}", file=sys.stderr)
    except Exception as e:
        print(f"[Database] Error creating directory {db_dir}: {e}", file=sys.stderr)
        raise
    
    return str(db_path)

# Ensure SQLite database directory exists
db_path = ensure_sqlite_db_path() if settings.DB_URL.startswith('sqlite:') else None

# Create SQLAlchemy engine with appropriate connection args
if settings.DB_URL.startswith('sqlite:'):
    # Use URI format that works on both Windows and Unix
    db_uri = f'sqlite:///{db_path}'
    engine = create_engine(
        db_uri,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True
    )
    
    # Enable foreign key constraints for SQLite
    @event.listens_for(engine, 'connect')
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
else:
    # For other database types
    engine = create_engine(settings.DB_URL, pool_pre_ping=True)

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
