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
from typing import Any, Dict, List, Optional, Tuple

import pytz
from sqlalchemy.orm import Session

from gluesync_scheduler.models.models import ExecutionMode, TaskType, TriggerFlow, TriggerFlowEvent, TriggerFlowExecutionLog, require_query_studio_fields, coerce_query_read_only
from gluesync_scheduler.models.trigger_schemas import (
    TriggerEventCreate,
    TriggerFlowCreate,
    TriggerFlowUpdate,
)
from gluesync_scheduler.services.chain_execution_service import (
    _get_chronos_callback_base,
    chain_execution_service,
)

logger = logging.getLogger(__name__)


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _build_trigger_url(flow_id: int) -> str:
    """Construct the fire URL for a TriggerFlow.

    Best-effort backend default for API responses / docs. The frontend
    overrides this with the browser-facing proxy URL
    (``window.location.origin + '/chronos'``).

    This is intentionally a concrete URL (via CHRONOS_CALLBACK_URL /
    SCHEDULER_INTERNAL_HOST), not ``{{chronos_address}}`` — that template
    is only for CoreHub→Chronos webhook callbacks.
    """
    base = _get_chronos_callback_base()
    return f"{base}/api/triggers/{flow_id}/fire"


def _events_to_orm(
    events: List[TriggerEventCreate],
    flow_id: int,
) -> List[TriggerFlowEvent]:
    """Convert a list of TriggerEventCreate Pydantic models to ORM objects."""
    result = []
    for pos, ev in enumerate(events):
        require_query_studio_fields(ev.task_type, ev.agent_id, ev.query_sql, ev.saved_query_id)
        orm = TriggerFlowEvent(
            trigger_flow_id=flow_id,
            position=pos,
            task_type=ev.task_type,
            pipeline_id=ev.pipeline_id,
            entity_ids=json.dumps(ev.entity_ids) if ev.entity_ids is not None else None,
            group_ids=json.dumps(ev.group_ids) if ev.group_ids is not None else None,
            with_snapshot=ev.with_snapshot,
            snapshot_write_method=ev.snapshot_write_method,
            agent_id=ev.agent_id,
            query_sql=ev.query_sql,
            saved_query_id=ev.saved_query_id,
            query_read_only=coerce_query_read_only(getattr(ev, "query_read_only", True)),
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
            platform_event=data.platform_event,
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

        # Register a webhook in CoreHub for platform event trigger flows
        if data.platform_event:
            try:
                chain_execution_service.sync_register_platform_event_webhook(
                    flow.id, data.platform_event
                )
            except Exception as exc:
                logger.error("Failed to register platform event webhook for flow %d: %s", flow.id, exc)

        return flow, token

    def update_flow(self, flow_id: int, data: TriggerFlowUpdate) -> Optional[TriggerFlow]:
        flow = self.db.query(TriggerFlow).filter(TriggerFlow.id == flow_id).first()
        if flow is None:
            return None

        old_platform_event = flow.platform_event

        if data.name is not None:
            flow.name = data.name
        if data.description is not None:
            flow.description = data.description
        if data.enabled is not None:
            flow.enabled = data.enabled
        if data.platform_event is not None:
            flow.platform_event = data.platform_event

        if data.events is not None:
            # Replace events atomically
            self.db.query(TriggerFlowEvent).filter(
                TriggerFlowEvent.trigger_flow_id == flow_id
            ).delete()
            for ev in _events_to_orm(data.events, flow_id):
                self.db.add(ev)

        self.db.commit()
        self.db.refresh(flow)

        # Re-register webhook if platform_event changed
        new_platform_event = flow.platform_event
        if data.platform_event is not None and new_platform_event != old_platform_event:
            if old_platform_event:
                try:
                    chain_execution_service.sync_delete_platform_event_webhook(flow_id)
                except Exception as exc:
                    logger.error("Failed to delete old platform event webhook for flow %d: %s", flow_id, exc)
            if new_platform_event:
                try:
                    chain_execution_service.sync_register_platform_event_webhook(flow_id, new_platform_event)
                except Exception as exc:
                    logger.error("Failed to register new platform event webhook for flow %d: %s", flow_id, exc)

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

        # Clean up the webhook from CoreHub for platform event trigger flows
        if flow.platform_event:
            try:
                chain_execution_service.sync_delete_platform_event_webhook(flow_id)
            except Exception as exc:
                logger.error("Failed to delete platform event webhook for flow %d: %s", flow_id, exc)

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

    async def fire(self, flow_id: int, source: str = "manual") -> Tuple[bool, str]:
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

        start_time = now.timestamp()
        try:
            success = await chain_execution_service.execute_trigger_flow(flow_id, events)
        except Exception as exc:
            logger.error("TriggerFlow %d raised an unhandled exception: %s", flow_id, exc)
            success = False
            self._record_error(flow_id, str(exc))
            self._record_execution_log(flow_id, "failed", source, str(exc), start_time)
            return False, str(exc)

        if success:
            flow = self.db.query(TriggerFlow).filter(TriggerFlow.id == flow_id).first()
            if flow:
                flow.last_successful_trigger = datetime.now(tz=timezone.utc)
                flow.last_error_message = None
                self.db.commit()
            self._record_execution_log(flow_id, "success", source, None, start_time)
        else:
            self._record_error(flow_id, "Chain stopped early — one or more sync events failed or timed out")
            self._record_execution_log(flow_id, "failed", source, "Chain stopped early — one or more sync events failed or timed out", start_time)

        return success, "" if success else "Chain stopped early"

    def _record_error(self, flow_id: int, message: str) -> None:
        flow = self.db.query(TriggerFlow).filter(TriggerFlow.id == flow_id).first()
        if flow:
            flow.last_error_message = message
            flow.last_trigger_error_time = datetime.now(tz=timezone.utc)
            self.db.commit()

    def _record_execution_log(
        self, flow_id: int, status: str, source: str, error_message: Optional[str], start_time: float
    ) -> None:
        """Record an execution log entry and prune old entries (keep last 20)."""
        duration_ms = int((datetime.now(tz=timezone.utc).timestamp() - start_time) * 1000)
        log = TriggerFlowExecutionLog(
            trigger_flow_id=flow_id,
            status=status,
            source=source,
            error_message=error_message,
            duration_ms=duration_ms,
        )
        self.db.add(log)
        self.db.commit()

        # Prune: keep only the most recent 20 entries per flow
        total = self.db.query(TriggerFlowExecutionLog).filter(
            TriggerFlowExecutionLog.trigger_flow_id == flow_id
        ).count()
        if total > 20:
            old_logs = (
                self.db.query(TriggerFlowExecutionLog)
                .filter(TriggerFlowExecutionLog.trigger_flow_id == flow_id)
                .order_by(TriggerFlowExecutionLog.triggered_at.desc())
                .offset(20)
                .all()
            )
            for old in old_logs:
                self.db.delete(old)
            self.db.commit()

    def get_execution_logs(self, flow_id: int, limit: int = 20) -> List[TriggerFlowExecutionLog]:
        """Return recent execution logs for a trigger flow, newest first."""
        return (
            self.db.query(TriggerFlowExecutionLog)
            .filter(TriggerFlowExecutionLog.trigger_flow_id == flow_id)
            .order_by(TriggerFlowExecutionLog.triggered_at.desc())
            .limit(limit)
            .all()
        )
