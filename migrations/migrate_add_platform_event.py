#!/usr/bin/env python3
"""
 * This file is part of Gluesync Scheduler Module (aka Chronos).
 *
 * Gluesync Scheduler Module (aka Chronos) is dual-licensed under the following licenses:
 *
 * 1. GNU General Public License (GPL) Version 3
 *    You may use, modify, and distribute this software under the terms of the GPL v3.
 *    See <http://www.gnu.org/licenses/gpl-3.0.html> for details.
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
from sqlalchemy import create_engine, text
from sqlalchemy.sql.ddl import DDL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

DB_URL = os.environ.get("DB_URL")
if not DB_URL:
    abs_data_dir = os.path.abspath(os.getenv('DATA_DIR', './data'))
    DB_URL = f'sqlite:///{abs_data_dir}/scheduler.db'
logger.info(f"Using DB_URL: {DB_URL}")


def add_platform_event_column(engine):
    from sqlalchemy import inspect

    logger.info(f"MIGRATION: Running migration against database: {engine.url}")

    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()

    if 'trigger_flows' not in existing_tables:
        logger.info("MIGRATION: trigger_flows table does not exist, skipping migration")
        return

    existing_columns = [col['name'] for col in inspector.get_columns('trigger_flows')]
    logger.info(f"MIGRATION: Current columns in trigger_flows: {existing_columns}")

    if 'platform_event' in existing_columns:
        logger.info("MIGRATION: platform_event column already exists, skipping migration")
        return

    logger.info("MIGRATION: Adding platform_event column")

    try:
        with engine.connect() as connection:
            add_column_ddl = DDL("ALTER TABLE trigger_flows ADD COLUMN platform_event VARCHAR")
            connection.execute(add_column_ddl)
            connection.commit()
            logger.info("MIGRATION: platform_event column added")

        inspector_after = inspect(engine)
        updated_columns = [col['name'] for col in inspector_after.get_columns('trigger_flows')]
        logger.info(f"MIGRATION: Columns after migration: {updated_columns}")

    except Exception as e:
        logger.error(f"Error during migration: {str(e)}")
        raise


def main():
    parser = argparse.ArgumentParser(description="Add platform_event column to trigger_flows table")
    parser.add_argument("--db-url", help="Database URL override")
    args = parser.parse_args()

    db_url = args.db_url if args.db_url else DB_URL
    logger.info(f"Running migration with database: {db_url}")

    try:
        engine = create_engine(db_url)
        add_platform_event_column(engine)
        logger.info("Migration completed successfully")
    except Exception as e:
        logger.error(f"Migration failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
