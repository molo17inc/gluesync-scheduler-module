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

import argparse
import logging
import os
import sys

from sqlalchemy import create_engine, inspect, text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

DB_URL = os.environ.get("DB_URL")
if not DB_URL:
    abs_data_dir = os.path.abspath(os.getenv("DATA_DIR", "./data"))
    DB_URL = f"sqlite:///{abs_data_dir}/scheduler.db"
logger.info(f"Using DB_URL: {DB_URL}")


def migrate_add_trigger_flows(engine) -> None:
    """Create trigger_flows and trigger_flow_events tables if they do not exist."""
    inspector = inspect(engine)
    existing = inspector.get_table_names()

    with engine.connect() as conn:
        if "trigger_flows" not in existing:
            logger.info("MIGRATION: Creating trigger_flows table")
            conn.execute(text("""
                CREATE TABLE trigger_flows (
                    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                    name                    VARCHAR NOT NULL,
                    description             VARCHAR,
                    enabled                 BOOLEAN NOT NULL DEFAULT 1,
                    secret_token            VARCHAR NOT NULL,
                    last_triggered          TIMESTAMP,
                    last_successful_trigger TIMESTAMP,
                    last_error_message      TEXT,
                    last_trigger_error_time TIMESTAMP,
                    created_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at              TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.commit()
            logger.info("MIGRATION: trigger_flows table created")
        else:
            logger.info("MIGRATION: trigger_flows already exists — skipping")

        if "trigger_flow_events" not in existing:
            logger.info("MIGRATION: Creating trigger_flow_events table")
            conn.execute(text("""
                CREATE TABLE trigger_flow_events (
                    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                    trigger_flow_id      INTEGER NOT NULL
                                             REFERENCES trigger_flows(id) ON DELETE CASCADE,
                    position             INTEGER NOT NULL,
                    task_type            VARCHAR NOT NULL,
                    pipeline_id          VARCHAR NOT NULL,
                    entity_ids           TEXT,
                    group_ids            TEXT,
                    with_snapshot        BOOLEAN NOT NULL DEFAULT 0,
                    snapshot_write_method VARCHAR NOT NULL DEFAULT 'UPSERT',
                    execution_mode       VARCHAR NOT NULL DEFAULT 'async',
                    created_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_trigger_flow_events_flow_id "
                "ON trigger_flow_events (trigger_flow_id)"
            ))
            conn.commit()
            logger.info("MIGRATION: trigger_flow_events table created")
        else:
            logger.info("MIGRATION: trigger_flow_events already exists — skipping")

    # Verify
    inspector2 = inspect(engine)
    tables_after = inspector2.get_table_names()
    for tbl in ("trigger_flows", "trigger_flow_events"):
        if tbl in tables_after:
            logger.info("MIGRATION: Verification OK — %s exists", tbl)
        else:
            logger.error("MIGRATION: Verification FAILED — %s not found after creation", tbl)


def main():
    parser = argparse.ArgumentParser(description="Add trigger_flows and trigger_flow_events tables")
    parser.add_argument("--db-url", help="Database URL override")
    args = parser.parse_args()

    db_url = args.db_url if args.db_url else DB_URL
    logger.info("Running migration with database: %s", db_url)

    try:
        engine = create_engine(db_url)
        migrate_add_trigger_flows(engine)
        logger.info("Migration completed successfully")
    except Exception as exc:
        logger.error("Migration failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
