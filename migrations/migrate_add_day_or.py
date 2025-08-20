#!/usr/bin/env python3
"""
Migration script to add day_or column to scheduled_jobs table.

This migration adds the day_or column to support croniter's day_or parameter,
which controls whether day-of-month and day-of-week are treated with OR logic (True)
or AND logic (False).
"""

import sqlite3
import sys
import os
import logging
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_database_path():
    """Get the database path from environment or use default"""
    db_url = os.getenv('DB_URL', 'sqlite:///./data/scheduler.db')
    
    # Extract the path from the SQLite URL
    if db_url.startswith('sqlite:///'):
        db_path = db_url[10:]  # Remove 'sqlite:///'
        
        # Handle relative paths
        if not os.path.isabs(db_path):
            db_path = os.path.join(project_root, db_path)
            
        return db_path
    else:
        raise ValueError(f"Unsupported database URL format: {db_url}")

def check_column_exists(cursor, table_name, column_name):
    """Check if a column exists in the table"""
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()
    return any(column[1] == column_name for column in columns)

def migrate_add_day_or():
    """Add day_or column to scheduled_jobs table"""
    
    try:
        # Get database path
        db_path = get_database_path()
        logger.info(f"Using database: {db_path}")
        
        # Ensure the database directory exists
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        
        # Connect to the database
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if the column already exists
        if check_column_exists(cursor, 'scheduled_jobs', 'day_or'):
            logger.info("Column 'day_or' already exists in scheduled_jobs table. Migration skipped.")
            conn.close()
            return True
        
        logger.info("Adding 'day_or' column to scheduled_jobs table...")
        
        # Add the day_or column with default value True (standard cron behavior)
        cursor.execute("""
            ALTER TABLE scheduled_jobs 
            ADD COLUMN day_or BOOLEAN NOT NULL DEFAULT 1
        """)
        
        # Verify the column was added
        if check_column_exists(cursor, 'scheduled_jobs', 'day_or'):
            logger.info("✓ Successfully added 'day_or' column to scheduled_jobs table")
            
            # Get count of existing jobs that will use the default value
            cursor.execute("SELECT COUNT(*) FROM scheduled_jobs")
            job_count = cursor.fetchone()[0]
            
            if job_count > 0:
                logger.info(f"✓ {job_count} existing jobs will use default day_or=True (OR logic)")
            else:
                logger.info("✓ No existing jobs found")
                
        else:
            logger.error("✗ Failed to add 'day_or' column")
            conn.rollback()
            conn.close()
            return False
        
        # Commit the changes
        conn.commit()
        conn.close()
        
        logger.info("✓ Migration completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"✗ Migration failed: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return False

def rollback_migration():
    """Rollback the migration (remove day_or column)"""
    
    try:
        # Get database path
        db_path = get_database_path()
        logger.info(f"Rolling back migration on database: {db_path}")
        
        # Connect to the database
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if the column exists
        if not check_column_exists(cursor, 'scheduled_jobs', 'day_or'):
            logger.info("Column 'day_or' does not exist. Nothing to rollback.")
            conn.close()
            return True
        
        logger.info("Removing 'day_or' column from scheduled_jobs table...")
        
        # SQLite doesn't support DROP COLUMN directly, so we need to recreate the table
        # First, get the current table schema
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='scheduled_jobs'")
        create_sql = cursor.fetchone()[0]
        
        # Create a backup table
        cursor.execute("""
            CREATE TABLE scheduled_jobs_backup AS 
            SELECT id, name, description, task_type, cron_expression, pipeline_id, 
                   entity_ids, with_snapshot, snapshot_write_method, enabled, command, 
                   cron_job_identifier, created_at, updated_at, last_run, next_run, 
                   last_successful_run, last_error_message, last_run_error_time, 
                   start_time, timezone_name, is_cron_expression
            FROM scheduled_jobs
        """)
        
        # Drop the original table
        cursor.execute("DROP TABLE scheduled_jobs")
        
        # Recreate the table without the day_or column
        cursor.execute("""
            CREATE TABLE scheduled_jobs (
                id INTEGER PRIMARY KEY,
                name VARCHAR NOT NULL,
                description VARCHAR,
                task_type VARCHAR NOT NULL,
                cron_expression VARCHAR NOT NULL,
                pipeline_id VARCHAR NOT NULL,
                entity_ids TEXT,
                with_snapshot BOOLEAN DEFAULT 0,
                snapshot_write_method VARCHAR NOT NULL DEFAULT 'UPSERT',
                enabled BOOLEAN DEFAULT 1,
                command TEXT NOT NULL,
                cron_job_identifier VARCHAR NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                last_run DATETIME,
                next_run DATETIME,
                last_successful_run DATETIME,
                last_error_message TEXT,
                last_run_error_time DATETIME,
                start_time VARCHAR,
                timezone_name VARCHAR,
                is_cron_expression BOOLEAN DEFAULT 0 NOT NULL
            )
        """)
        
        # Restore the data
        cursor.execute("""
            INSERT INTO scheduled_jobs 
            SELECT * FROM scheduled_jobs_backup
        """)
        
        # Drop the backup table
        cursor.execute("DROP TABLE scheduled_jobs_backup")
        
        # Commit the changes
        conn.commit()
        conn.close()
        
        logger.info("✓ Migration rollback completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"✗ Migration rollback failed: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return False

def main():
    """Main function to run the migration"""
    
    if len(sys.argv) > 1 and sys.argv[1] == '--rollback':
        logger.info("Starting migration rollback...")
        success = rollback_migration()
    else:
        logger.info("Starting migration...")
        success = migrate_add_day_or()
    
    if success:
        logger.info("Migration operation completed successfully")
        sys.exit(0)
    else:
        logger.error("Migration operation failed")
        sys.exit(1)

if __name__ == "__main__":
    main()
