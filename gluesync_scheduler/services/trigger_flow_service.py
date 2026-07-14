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

import asyncio
import json
import logging
import os
import secrets
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import pytz
from sqlalchemy.orm import Session

from gluesync_scheduler.models.models import ExecutionMode, TaskType, TriggerFlow, TriggerFlowEvent
from gluesync_scheduler.models.trigger_schemas import (
    TriggerEventCreate,
    TriggerFlowCreate,
    TriggerFlowUpdate,
)
from gluesync_scheduler.services.chain_execution_service import chain_execution_service

logger = logging.getLogger(__name__)


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _build_trigger_url(flow_id: int) -> str:
    """Construct the public-facing fire URL for a TriggerFlow."""
    base = os.getenv("CHRONOS_CALLBACK_URL", "").rstrip("/")
    if not base:
        ssl_enabled = os.getenv("SSL_ENABLED", "False").lower() in ("true", "1", "t")
        proto = "https" if ssl_enabled else "http"
        host = os.getenv("SCHEDULER_INTERNAL_HOST", "localhost")
        port = int(os.getenv("PORT", "8000"))
        base = f"{proto}://{host}:{port}"
    return f"{base}/api/triggers/{flow_id}/fire"


def _events_to_orm(
    events: List[TriggerEventCreate],
    flow_id: int,
) -> List[TriggerFlowEvent]:
    """Convert a list of TriggerEventCreate Pydantic models to ORM objects."""
    result = []
    for pos, ev in enumerate(events):
        orm = TriggerFlowEvent(
            trigger_flow_id=flow_id,
            position=pos,
            task_type=ev.task_type,
            pipeline_id=ev.pipeline_id,
            entity_ids=json.dumps(ev.entity_ids) if ev.entity_ids is not None else None,
            group_ids=json.dumps(ev.group_ids) if ev.group_ids is not None else None,
            with_snapshot=ev.with_snapshot,
            snapshot_write_method=ev.snapshot_write_method,
            execution_mode=ExecutionMode(ev.execution_mode.value),
        )
        result.append(orm)
    return result


def _attach_trigger_url(flow: TriggerFlow) -> TriggerFlow:
    """Monkey-patch a transient `trigger_url` attribute so Pydantic can read it."""
    flow.trigger_url = _build_trigger_url(flow.id)  # type: ignore[attr-defined]
    return flow


class TriggerFlowService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def list_flows(self, skip: int = 0, limit: int = 100) -> Tuple[List[TriggerFlow], int]:
        q = self.db.query(TriggerFlow)
        total = q.count()
        flows = q.offset(skip).limit(limit).all()
        for f in flows:
            _attach_trigger_url(f)
            f.events = (  # type: ignore[attr-defined]
                self.db.query(TriggerFlowEvent)
                .filter(TriggerFlowEvent.trigger_flow_id == f.id)
                .order_by(TriggerFlowEvent.position)
                .all()
            )
        return flows, total

    def get_flow(self, flow_id: int) -> Optional[TriggerFlow]:
        flow = self.db.query(TriggerFlow).filter(TriggerFlow.id == flow_id).first()
        if flow is None:
            return None
        _attach_trigger_url(flow)
        flow.events = (  # type: ignore[attr-defined]
            self.db.query(TriggerFlowEvent)
            .filter(TriggerFlowEvent.trigger_flow_id == flow.id)
            .order_by(TriggerFlowEvent.position)
            .all()
        )
        return flow

    def create_flow(self, data: TriggerFlowCreate) -> Tuple[TriggerFlow, str]:
        """Create a new TriggerFlow.

        Returns (flow, plaintext_token) — the token is only handed back here.
        """
        token = _generate_token()
        flow = TriggerFlow(
            name=data.name,
            description=data.description,
            enabled=data.enabled,
            secret_token=token,
        )
        self.db.add(flow)
        self.db.flush()  # get the generated ID without committing

        orm_events = _events_to_orm(data.events, flow.id)
        for ev in orm_events:
            self.db.add(ev)

        self.db.commit()
        self.db.refresh(flow)
        flow.events = orm_events  # type: ignore[attr-defined]
        _attach_trigger_url(flow)
        return flow, token

    def update_flow(self, flow_id: int, data: TriggerFlowUpdate) -> Optional[TriggerFlow]:
        flow = self.db.query(TriggerFlow).filter(TriggerFlow.id == flow_id).first()
        if flow is None:
            return None

        if data.name is not None:
            flow.name = data.name
        if data.description is not None:
            flow.description = data.description
        if data.enabled is not None:
            flow.enabled = data.enabled

        if data.events is not None:
            # Replace events atomically
            self.db.query(TriggerFlowEvent).filter(
                TriggerFlowEvent.trigger_flow_id == flow_id
            ).delete()
            for ev in _events_to_orm(data.events, flow_id):
                self.db.add(ev)

        self.db.commit()
        self.db.refresh(flow)
        return self.get_flow(flow_id)

    def toggle_enabled(self, flow_id: int, enabled: bool) -> Optional[TriggerFlow]:
        flow = self.db.query(TriggerFlow).filter(TriggerFlow.id == flow_id).first()
        if flow is None:
            return None
        flow.enabled = enabled
        self.db.commit()
        self.db.refresh(flow)
        return self.get_flow(flow_id)

    def delete_flow(self, flow_id: int) -> bool:
        flow = self.db.query(TriggerFlow).filter(TriggerFlow.id == flow_id).first()
        if flow is None:
            return False
        self.db.delete(flow)
        self.db.commit()
        return True

    def regenerate_token(self, flow_id: int) -> Optional[Tuple[TriggerFlow, str]]:
        flow = self.db.query(TriggerFlow).filter(TriggerFlow.id == flow_id).first()
        if flow is None:
            return None
        token = _generate_token()
        flow.secret_token = token
        self.db.commit()
        self.db.refresh(flow)
        return self.get_flow(flow_id), token

    # ------------------------------------------------------------------
    # Token verification
    # ------------------------------------------------------------------

    def verify_token(self, flow_id: int, token: str) -> Optional[TriggerFlow]:
        """Return the TriggerFlow if the token matches, else None."""
        flow = self.db.query(TriggerFlow).filter(TriggerFlow.id == flow_id).first()
        if flow is None:
            return None
        # Constant-time comparison to avoid timing attacks
        if not secrets.compare_digest(flow.secret_token, token):
            return None
        return flow

    # ------------------------------------------------------------------
    # Fire
    # ------------------------------------------------------------------

    async def fire(self, flow_id: int) -> Tuple[bool, str]:
        """Execute the TriggerFlow chain.

        Updates last_triggered on the flow record.
        Returns (success, error_message).
        """
        flow = self.db.query(TriggerFlow).filter(TriggerFlow.id == flow_id).first()
        if flow is None:
            return False, "TriggerFlow not found"

        events = (
            self.db.query(TriggerFlowEvent)
            .filter(TriggerFlowEvent.trigger_flow_id == flow_id)
            .order_by(TriggerFlowEvent.position)
            .all()
        )

        now = datetime.now(tz=timezone.utc)
        flow.last_triggered = now
        self.db.commit()

        try:
            success = await chain_execution_service.execute_trigger_flow(flow_id, events)
        except Exception as exc:
            logger.error("TriggerFlow %d raised an unhandled exception: %s", flow_id, exc)
            success = False
            self._record_error(flow_id, str(exc))
            return False, str(exc)

        if success:
            flow = self.db.query(TriggerFlow).filter(TriggerFlow.id == flow_id).first()
            if flow:
                flow.last_successful_trigger = datetime.now(tz=timezone.utc)
                flow.last_error_message = None
                self.db.commit()
        else:
            self._record_error(flow_id, "Chain stopped early — one or more sync events failed or timed out")

        return success, "" if success else "Chain stopped early"

    def _record_error(self, flow_id: int, message: str) -> None:
        flow = self.db.query(TriggerFlow).filter(TriggerFlow.id == flow_id).first()
        if flow:
            flow.last_error_message = message
            flow.last_trigger_error_time = datetime.now(tz=timezone.utc)
            self.db.commit()
