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


def add_is_cron_expression_column(engine):
    """Add the is_cron_expression column to the scheduled_jobs table using SQLAlchemy schema operations"""
    
    from sqlalchemy import MetaData, Table, Column, Boolean
    from sqlalchemy.sql.ddl import DDL
    
    logger.info(f"MIGRATION: Running SQLAlchemy-based migration against database: {engine.url}")
    
    # Create metadata and reflect existing schema
    metadata = MetaData()
    metadata.reflect(bind=engine)
    
    # Check if the scheduled_jobs table exists
    if 'scheduled_jobs' not in metadata.tables:
        logger.info("MIGRATION: scheduled_jobs table does not exist, skipping migration")
        return
    
    scheduled_jobs_table = metadata.tables['scheduled_jobs']
    existing_columns = [col.name for col in scheduled_jobs_table.columns]
    logger.info(f"MIGRATION: Current columns in scheduled_jobs: {existing_columns}")
    
    # Check if the is_cron_expression column already exists
    if 'is_cron_expression' in existing_columns:
        logger.info("MIGRATION: is_cron_expression column already exists, skipping migration")
        return
    
    logger.info("MIGRATION: Adding is_cron_expression column using SQLAlchemy DDL operations")
    
    try:
        # Use SQLAlchemy's DDL approach for adding columns
        with engine.connect() as connection:
            # Create DDL statement for adding the column
            add_column_ddl = DDL("ALTER TABLE scheduled_jobs ADD COLUMN is_cron_expression BOOLEAN NOT NULL DEFAULT 0")
            connection.execute(add_column_ddl)
            connection.commit()
            
            logger.info("MIGRATION: SQLAlchemy DDL operation completed")
        
        # Verify the column was added by reflecting the schema again
        metadata_after = MetaData()
        metadata_after.reflect(bind=engine)
        updated_table = metadata_after.tables['scheduled_jobs']
        updated_columns = [col.name for col in updated_table.columns]
        
        logger.info(f"MIGRATION: Columns after migration: {updated_columns}")
        
        if 'is_cron_expression' in updated_columns:
            logger.info("MIGRATION: SQLAlchemy DDL migration verified: is_cron_expression column exists")
            
            # Test querying the new column
            with engine.connect() as connection:
                try:
                    result = connection.execute(text("SELECT is_cron_expression FROM scheduled_jobs LIMIT 1"))
                    logger.info("MIGRATION: Successfully queried is_cron_expression column")
                except Exception as query_error:
                    logger.error(f"MIGRATION: Failed to query is_cron_expression column: {query_error}")
        else:
            logger.error("MIGRATION: SQLAlchemy DDL migration failed: is_cron_expression column not found after migration")
                
    except Exception as e:
        logger.error(f"Error during SQLAlchemy DDL migration: {str(e)}")
        raise


def main():
    """Main function to run the migration"""
    parser = argparse.ArgumentParser(description="Add is_cron_expression column to scheduled_jobs table")
    parser.add_argument("--db-url", help="Database URL override")
    args = parser.parse_args()
    
    # Use command-line DB URL if provided
    db_url = args.db_url if args.db_url else DB_URL
    
    logger.info(f"Running migration with database: {db_url}")
    
    try:
        # Create engine
        engine = create_engine(db_url)
        
        # Run the migration
        add_is_cron_expression_column(engine)
        
        logger.info("Migration completed successfully")
        
    except Exception as e:
        logger.error(f"Migration failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
