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
from sqlalchemy import create_engine, inspect, text

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    # Try to import settings from the project
    from gluesync_scheduler.config.settings import settings
    DB_URL = settings.DB_URL
    logger.info(f"Using DB_URL from settings: {DB_URL}")
except ImportError:
    # If importing fails, use the same path as the application
    DB_URL = "sqlite:///./data/scheduler.db"
    logger.warning(f"Could not import settings, using default DB_URL: {DB_URL}")


def add_snapshot_write_method_column(engine):
    """Add the snapshot_write_method column to the scheduled_jobs table if it doesn't exist"""
    
    logger.info(f"Running migration against database: {engine.url}")
    
    inspector = inspect(engine)
    
    # Check if the scheduled_jobs table exists
    table_names = inspector.get_table_names()
    logger.info(f"Available tables: {table_names}")
    
    if 'scheduled_jobs' not in table_names:
        logger.info("scheduled_jobs table does not exist, skipping migration")
        return
    
    # Check if the snapshot_write_method column already exists
    columns = inspector.get_columns('scheduled_jobs')
    column_names = [col['name'] for col in columns]
    logger.info(f"Current columns in scheduled_jobs: {column_names}")
    
    if 'snapshot_write_method' in column_names:
        logger.info("snapshot_write_method column already exists, skipping migration")
        return
    
    logger.info("Adding snapshot_write_method column to scheduled_jobs table")
    
    try:
        with engine.connect() as connection:
            # Add the new column with default value 'UPSERT'
            logger.info("Executing: ALTER TABLE scheduled_jobs ADD COLUMN snapshot_write_method VARCHAR NOT NULL DEFAULT 'UPSERT'")
            connection.execute(text(
                "ALTER TABLE scheduled_jobs ADD COLUMN snapshot_write_method VARCHAR NOT NULL DEFAULT 'UPSERT'"
            ))
            connection.commit()
            logger.info("Successfully added snapshot_write_method column")
            
            # Verify the column was added
            inspector_after = inspect(engine)
            columns_after = inspector_after.get_columns('scheduled_jobs')
            column_names_after = [col['name'] for col in columns_after]
            logger.info(f"Columns after migration: {column_names_after}")
            
            if 'snapshot_write_method' in column_names_after:
                logger.info("✅ Migration verified: snapshot_write_method column exists")
            else:
                logger.error("❌ Migration failed: snapshot_write_method column not found after migration")
                
    except Exception as e:
        logger.error(f"Error during migration: {str(e)}")
        raise


def main():
    """Main function to run the migration"""
    parser = argparse.ArgumentParser(description="Add snapshot_write_method column to scheduled_jobs table")
    parser.add_argument("--db-url", help="Database URL override")
    args = parser.parse_args()
    
    # Use command-line DB URL if provided
    db_url = args.db_url if args.db_url else DB_URL
    logger.info(f"Running migration with database: {db_url}")
    
    try:
        # Create engine
        engine = create_engine(db_url)
        
        # Run the migration
        add_snapshot_write_method_column(engine)
        
        logger.info("Migration completed successfully")
        
    except Exception as e:
        logger.error(f"Migration failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
