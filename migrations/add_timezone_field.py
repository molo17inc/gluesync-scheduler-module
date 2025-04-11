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
from datetime import datetime
import pytz

# Add the parent directory to the path so we can import from the root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import settings

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def migrate():
    """
    Add timezone field to the scheduled_jobs table and update datetime fields to include timezone info.
    """
    logger.info("Starting migration to add timezone field to scheduled_jobs table")
    
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
        
        # Check if timezone column already exists
        cursor.execute("PRAGMA table_info(scheduled_jobs)")
        columns = cursor.fetchall()
        column_names = [col[1] for col in columns]
        
        if 'timezone' not in column_names:
            # Add the timezone column with the default value from settings
            logger.info(f"Adding timezone column with default value: {settings.TIMEZONE}")
            cursor.execute(f"ALTER TABLE scheduled_jobs ADD COLUMN timezone TEXT DEFAULT '{settings.TIMEZONE}'")
            logger.info("Timezone column added successfully")
        else:
            logger.info("Timezone column already exists, skipping")
        
        # Update datetime columns to ensure they have timezone info
        # SQLite doesn't support modifying column types directly, so we need to:
        # 1. Create a new table with the correct column types
        # 2. Copy data from the old table to the new table
        # 3. Drop the old table
        # 4. Rename the new table to the original name
        
        # First, check if the datetime columns already have timezone info
        # If they do, we can skip this part
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='scheduled_jobs'")
        table_def = cursor.fetchone()[0]
        
        # Check if all datetime columns already have timezone=True
        datetime_columns_with_timezone = all(
            "timezone=True" in table_def.lower() for col in 
            ["last_run", "next_run", "last_successful_run", "last_run_error_time"]
        )
        
        if not datetime_columns_with_timezone:
            logger.info("Updating datetime columns to include timezone information")
            
            # Get the current timezone
            tz = pytz.timezone(settings.TIMEZONE)
            
            # Backup the current data
            cursor.execute("SELECT * FROM scheduled_jobs")
            rows = cursor.fetchall()
            
            # Get column names
            cursor.execute("PRAGMA table_info(scheduled_jobs)")
            columns = cursor.fetchall()
            column_names = [col[1] for col in columns]
            
            # Create a temporary table with the correct column types
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
                last_run_error_time TIMESTAMP WITH TIME ZONE,
                timezone TEXT NOT NULL DEFAULT '{settings.TIMEZONE}'
            )
            """
            
            cursor.execute("DROP TABLE IF EXISTS scheduled_jobs_new")
            cursor.execute(create_temp_table_sql)
            
            # Copy data to the new table
            for row in rows:
                # Convert row to a dictionary for easier handling
                row_dict = dict(zip(column_names, row))
                
                # Add timezone info to datetime fields if they exist and are not None
                datetime_fields = ['last_run', 'next_run', 'last_successful_run', 'last_run_error_time']
                for field in datetime_fields:
                    if field in row_dict and row_dict[field] is not None:
                        # Parse the datetime string
                        try:
                            dt = datetime.fromisoformat(row_dict[field])
                            # Add timezone info if not present
                            if dt.tzinfo is None:
                                dt = tz.localize(dt)
                            # Update the value in the dictionary
                            row_dict[field] = dt.isoformat()
                        except (ValueError, TypeError):
                            # If we can't parse it, leave it as is
                            pass
                
                # Create the INSERT statement
                placeholders = ", ".join(["?"] * len(row_dict))
                columns_str = ", ".join(row_dict.keys())
                sql = f"INSERT INTO scheduled_jobs_new ({columns_str}) VALUES ({placeholders})"
                
                # Execute the INSERT
                cursor.execute(sql, list(row_dict.values()))
            
            # Replace the old table with the new one
            cursor.execute("DROP TABLE scheduled_jobs")
            cursor.execute("ALTER TABLE scheduled_jobs_new RENAME TO scheduled_jobs")
            
            logger.info("Successfully updated datetime columns with timezone information")
        else:
            logger.info("Datetime columns already have timezone information, skipping")
        
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
