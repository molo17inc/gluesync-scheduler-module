#!/usr/bin/env python3
"""
Database migration to add group_ids field to scheduled_jobs table
for supporting group-level scheduling functionality.
"""

import sqlite3
import sys
import os

def migrate_add_group_ids_field(db_path):
    """
    Add group_ids field to the scheduled_jobs table to support group-level scheduling.
    
    Args:
        db_path (str): Path to the SQLite database file
    """
    try:
        # Connect to the database
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if the group_ids column already exists
        cursor.execute("PRAGMA table_info(scheduled_jobs)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'group_ids' not in columns:
            print("Adding group_ids column to scheduled_jobs table...")
            
            # Add the group_ids column
            cursor.execute("""
                ALTER TABLE scheduled_jobs 
                ADD COLUMN group_ids TEXT
            """)
            
            conn.commit()
            print("Successfully added group_ids column to scheduled_jobs table.")
        else:
            print("group_ids column already exists in scheduled_jobs table.")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error during migration: {str(e)}")
        return False

def main():
    """Main function to run the migration"""
    # Default database path
    default_db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'scheduler.db')
    
    # Use provided path or default
    db_path = sys.argv[1] if len(sys.argv) > 1 else default_db_path
    
    print(f"Running migration on database: {db_path}")
    
    if not os.path.exists(db_path):
        print(f"Database file does not exist: {db_path}")
        sys.exit(1)
    
    success = migrate_add_group_ids_field(db_path)
    
    if success:
        print("Migration completed successfully!")
        sys.exit(0)
    else:
        print("Migration failed!")
        sys.exit(1)

if __name__ == "__main__":
    main()
