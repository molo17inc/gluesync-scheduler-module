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
from sqlalchemy import create_engine, inspect, text, MetaData

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Get database URL from environment (settings system may not be fully available during migration)
DB_URL = os.environ.get("DB_URL")
if not DB_URL:
    abs_data_dir = os.path.abspath(os.getenv('DATA_DIR', './data'))
    DB_URL = f'sqlite:///{abs_data_dir}/scheduler.db'
logger.info(f"Using DB_URL: {DB_URL}")


def create_chained_job_events_table(engine):
    """Create the chained_job_events table if it does not already exist."""
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()

    if "chained_job_events" in existing_tables:
        logger.info("MIGRATION: chained_job_events table already exists — skipping creation")
        return

    logger.info("MIGRATION: Creating chained_job_events table")
    with engine.connect() as connection:
        connection.execute(text("""
            CREATE TABLE chained_job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_job_id INTEGER NOT NULL REFERENCES scheduled_jobs(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                task_type VARCHAR NOT NULL,
                pipeline_id VARCHAR NOT NULL,
                entity_ids TEXT,
                group_ids TEXT,
                with_snapshot BOOLEAN NOT NULL DEFAULT 0,
                snapshot_write_method VARCHAR NOT NULL DEFAULT 'UPSERT',
                execution_mode VARCHAR NOT NULL DEFAULT 'async',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_chained_job_events_parent_job_id "
            "ON chained_job_events (parent_job_id)"
        ))
        connection.commit()

    logger.info("MIGRATION: chained_job_events table created successfully")

    # Verify
    inspector2 = inspect(engine)
    tables_after = inspector2.get_table_names()
    if "chained_job_events" in tables_after:
        logger.info("MIGRATION: Verification OK — chained_job_events exists")
    else:
        logger.error("MIGRATION: Verification FAILED — table not found after creation")


def main():
    parser = argparse.ArgumentParser(description="Add chained_job_events table")
    parser.add_argument("--db-url", help="Database URL override")
    args = parser.parse_args()

    db_url = args.db_url if args.db_url else DB_URL
    logger.info(f"Running migration with database: {db_url}")

    try:
        engine = create_engine(db_url)
        create_chained_job_events_table(engine)
        logger.info("Migration completed successfully")
    except Exception as e:
        logger.error(f"Migration failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
