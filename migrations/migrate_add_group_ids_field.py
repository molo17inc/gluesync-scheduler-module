#!/usr/bin/env python3
"""
Database migration to add group_ids field to scheduled_jobs table
for supporting group-level scheduling functionality.
"""

import os
import sys
from pathlib import Path

def migrate_add_group_ids_field(engine):
    """
    Add group_ids field to the scheduled_jobs table to support group-level scheduling.
    
    Args:
        engine: SQLAlchemy database engine
    """
    try:
        from sqlalchemy import MetaData, Table, Column, String, text
        
        # Create metadata and reflect existing schema
        metadata = MetaData()
        metadata.reflect(bind=engine)
        
        # Check if the scheduled_jobs table exists
        if 'scheduled_jobs' not in metadata.tables:
            print("scheduled_jobs table does not exist, skipping migration")
            return False
        
        scheduled_jobs_table = metadata.tables['scheduled_jobs']
        existing_columns = [col.name for col in scheduled_jobs_table.columns]
        
        if 'group_ids' not in existing_columns:
            print("Adding group_ids column to scheduled_jobs table...")
            
            try:
                # Use SQLAlchemy DDL approach for adding columns
                from sqlalchemy.sql.ddl import DDL
                with engine.connect() as connection:
                    # Create DDL statement for adding the column
                    add_column_ddl = DDL("ALTER TABLE scheduled_jobs ADD COLUMN group_ids TEXT")
                    connection.execute(add_column_ddl)
                    connection.commit()
                    
                    print("Successfully added group_ids column to scheduled_jobs table.")
            except Exception as ddl_error:
                print(f"DDL approach failed: {ddl_error}")
                # Fallback to raw SQL execution
                try:
                    with engine.connect() as connection:
                        connection.execute(text("ALTER TABLE scheduled_jobs ADD COLUMN group_ids TEXT"))
                        connection.commit()
                        print("Successfully added group_ids column using raw SQL.")
                except Exception as raw_error:
                    print(f"Raw SQL approach also failed: {raw_error}")
                    raise
        else:
            print("group_ids column already exists in scheduled_jobs table.")
        
        return True
        
    except Exception as e:
        print(f"Error during migration: {str(e)}")
        return False


def main():
    """Main function to run the migration"""
    # Get database URL from environment (same logic as main app)
    db_url = os.environ.get("DB_URL")
    if not db_url:
        # Convert relative path to absolute path for consistency
        abs_data_dir = os.path.abspath(os.getenv('DATA_DIR', './data'))
        db_url = f'sqlite:///{abs_data_dir}/scheduler.db'
    
    # Extract database path from URL
    if db_url.startswith('sqlite:///'):
        db_path = db_url.replace('sqlite:///', '')
        # Handle Windows paths
        if ':' in db_path and len(db_path) > 2 and db_path[1] == ':':
            db_path = db_path[2:]  # Remove the leading / from /C:/path
    else:
        # Fallback to old logic for backward compatibility
        db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'scheduler.db')
    
    print(f"Running migration on database: {db_path}")
    
    if not os.path.exists(db_path):
        print(f"Database file does not exist: {db_path}")
        sys.exit(1)
    
    # Import SQLAlchemy and create engine
    from sqlalchemy import create_engine
    engine = create_engine(db_url)
    
    success = migrate_add_group_ids_field(engine)
    
    if success:
        print("Migration completed successfully!")
        sys.exit(0)
    else:
        print("Migration failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()
