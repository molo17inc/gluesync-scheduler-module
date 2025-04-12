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
import sqlite3
import logging

# Add the parent directory to the path so we can import from the root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import settings

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def migrate():
    """
    Remove the timezone field from the scheduled_jobs table as it's redundant.
    All datetime fields already have timezone information stored with them.
    """
    logger.info("Starting migration to remove timezone field from scheduled_jobs table")
    
    # Get the database URL from settings
    db_url = settings.DB_URL
    
    # Extract the database file path from the SQLite URL
    if db_url.startswith('sqlite:///'):
        db_path = db_url[10:]  # Remove 'sqlite:///' prefix
    else:
        logger.error(f"Unsupported database URL format: {db_url}")
        return
    
    logger.info(f"Using database at: {db_path}")
    
    # Ensure the database file exists
    if not os.path.exists(db_path):
        logger.error(f"Database file not found: {db_path}")
        return
    
    # Connect to the database
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        logger.info("Connected to the database")
        
        # Check if timezone column exists
        cursor.execute("PRAGMA table_info(scheduled_jobs)")
        columns = cursor.fetchall()
        column_names = [col[1] for col in columns]
        
        if 'timezone' in column_names:
            logger.info("Removing timezone column from scheduled_jobs table")
            
            # In SQLite, we need to create a new table without the column, copy data, and rename
            # Get the current table definition
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='scheduled_jobs'")
            table_def = cursor.fetchone()[0]
            
            # Create a temporary table with all columns except timezone
            create_temp_table_sql = """
            CREATE TABLE scheduled_jobs_new (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                task_type TEXT NOT NULL,
                cron_expression TEXT NOT NULL,
                pipeline_id TEXT NOT NULL,
                entity_ids TEXT,
                with_snapshot BOOLEAN DEFAULT 0,
                enabled BOOLEAN DEFAULT 1,
                command TEXT NOT NULL,
                cron_job_identifier TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_run TIMESTAMP WITH TIME ZONE,
                next_run TIMESTAMP WITH TIME ZONE,
                last_successful_run TIMESTAMP WITH TIME ZONE,
                last_error_message TEXT,
                last_run_error_time TIMESTAMP WITH TIME ZONE
            )
            """
            
            cursor.execute("DROP TABLE IF EXISTS scheduled_jobs_new")
            cursor.execute(create_temp_table_sql)
            
            # Get column names without timezone
            columns_without_timezone = [col for col in column_names if col != 'timezone']
            columns_str = ', '.join(columns_without_timezone)
            
            # Copy data from old table to new table
            cursor.execute(f"INSERT INTO scheduled_jobs_new ({columns_str}) SELECT {columns_str} FROM scheduled_jobs")
            
            # Drop old table and rename new table
            cursor.execute("DROP TABLE scheduled_jobs")
            cursor.execute("ALTER TABLE scheduled_jobs_new RENAME TO scheduled_jobs")
            
            logger.info("Successfully removed timezone column from scheduled_jobs table")
        else:
            logger.info("Timezone column does not exist, no migration needed")
        
        # Commit the changes
        conn.commit()
        logger.info("Migration completed successfully")
        
    except sqlite3.Error as e:
        logger.error(f"SQLite error: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()
            logger.info("Database connection closed")

if __name__ == "__main__":
    migrate()
