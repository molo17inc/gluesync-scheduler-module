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

import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Enum, Text, Boolean
from sqlalchemy.sql.expression import text
from sqlalchemy.sql.sqltypes import TIMESTAMP

from gluesync_scheduler.config.settings import settings
from gluesync_scheduler.db.database import Base


class TaskType(enum.Enum):
    ENTITY_START = "entity_start"
    ENTITY_STOP = "entity_stop"
    PIPELINE_START = "pipeline_start"
    PIPELINE_STOP = "pipeline_stop"
    ENTITY_SNAPSHOT = "entity_snapshot"
    PIPELINE_SNAPSHOT = "pipeline_snapshot"
    GROUP_START = "group_start"
    GROUP_STOP = "group_stop"
    GROUP_SNAPSHOT = "group_snapshot"


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    task_type = Column(Enum(TaskType), nullable=False)
    cron_expression = Column(String, nullable=False)
    pipeline_id = Column(String, nullable=False)
    entity_ids = Column(Text, nullable=True)
    group_ids = Column(Text, nullable=True)
    with_snapshot = Column(Boolean, default=False)
    enabled = Column(Boolean, default=True)
    command = Column(Text, nullable=False)
    cron_job_identifier = Column(String, nullable=False, unique=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP"))
    last_run = Column(DateTime(timezone=True), nullable=True)
    next_run = Column(DateTime(timezone=True), nullable=True)
    last_successful_run = Column(DateTime(timezone=True), nullable=True)
    last_error_message = Column(Text, nullable=True)
    last_run_error_time = Column(DateTime(timezone=True), nullable=True)
    start_time = Column(String, nullable=True)
    timezone_name = Column(String, nullable=True)


class Setting(Base):
    """Model for storing application settings as key-value pairs"""
    __tablename__ = "settings"
    
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, nullable=False, unique=True, index=True)
    value = Column(String, nullable=True)
    description = Column(String, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP"), onupdate=text("CURRENT_TIMESTAMP"))
