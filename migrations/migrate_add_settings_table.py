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
import logging
import argparse
from datetime import datetime
from pathlib import Path
from sqlalchemy import Column, Integer, String, DateTime, create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Get database URL from environment (settings system not yet available during migration)
DB_URL = os.environ.get("DB_URL", "sqlite:///./data/scheduler.db")
logger.info(f"Using DB_URL: {DB_URL}")

# Create a base class for declarative models
Base = declarative_base()

class Setting(Base):
    """Model for storing application settings as key-value pairs"""
    __tablename__ = "settings"
    
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, nullable=False, unique=True, index=True)
    value = Column(String, nullable=True)
    description = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


def create_settings_table(engine):
    """Create the settings table if it doesn't exist"""
    inspector = inspect(engine)
    if "settings" not in inspector.get_table_names():
        logger.info("Creating settings table...")
        Setting.__table__.create(engine)
        logger.info("Settings table created successfully.")
        
        # Initialize with default timezone setting from environment
        try:
            Session = sessionmaker(bind=engine)
            session = Session()
            
            # Add default timezone setting
            default_timezone = os.getenv('TIMEZONE', 'UTC')
            logger.info(f"Initializing default timezone setting: {default_timezone}")
            
            session.execute(
                text(
                    "INSERT INTO settings (key, value, description, created_at, updated_at) "
                    "VALUES (:key, :value, :description, :created_at, :updated_at)"
                ),
                {
                    "key": "timezone",
                    "value": default_timezone,
                    "description": "Timezone used for scheduling jobs",
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow()
                }
            )
            session.commit()
            logger.info("Default settings initialized.")
            
        except Exception as e:
            logger.error(f"Error initializing default settings: {e}")
    else:
        logger.info("Settings table already exists. No changes made.")


def main():
    """Main function to run the migration"""
    parser = argparse.ArgumentParser(description="Create the settings table in the database")
    parser.add_argument(
        "--db-url",
        type=str,
        default=DB_URL,
        help="Database URL (default: from environment or settings)"
    )
    args = parser.parse_args()
    
    db_url = args.db_url
    
    # Create necessary directories
    if db_url.startswith("sqlite:///"):
        db_path = db_url.replace("sqlite:///", "")
        if db_path.startswith("./"):
            db_path = db_path[2:]
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
            logger.info(f"Created directory: {db_dir}")
    
    # Create the engine and run the migration
    engine = create_engine(db_url)
    create_settings_table(engine)
    logger.info("Migration completed successfully.")


if __name__ == "__main__":
    main()
