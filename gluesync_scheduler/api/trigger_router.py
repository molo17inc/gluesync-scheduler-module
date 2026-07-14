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
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Header, HTTPException, Path, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from gluesync_scheduler.db.database import get_db
from gluesync_scheduler.models.trigger_schemas import (
    FireResponse,
    FireStatus,
    TriggerFlowCreate,
    TriggerFlowCreateResponse,
    TriggerFlowList,
    TriggerFlowResponse,
    TriggerFlowUpdate,
)
from gluesync_scheduler.services.trigger_flow_service import TriggerFlowService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/triggers",
    tags=["triggers"],
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Trigger flow not found"},
        status.HTTP_400_BAD_REQUEST: {"description": "Invalid request data"},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Server error"},
    },
)


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/",
    response_model=TriggerFlowList,
    summary="List all trigger flows",
)
def list_trigger_flows(
    skip: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(100, ge=1, le=1000, description="Page size"),
    db: Session = Depends(get_db),
):
    """Return a paginated list of all TriggerFlows (secret tokens excluded)."""
    svc = TriggerFlowService(db)
    flows, total = svc.list_flows(skip=skip, limit=limit)
    return {"items": flows, "total": total}


@router.get(
    "/{flow_id}",
    response_model=TriggerFlowResponse,
    summary="Get a single trigger flow",
)
def get_trigger_flow(
    flow_id: int = Path(..., description="TriggerFlow ID"),
    db: Session = Depends(get_db),
):
    """Retrieve a TriggerFlow by ID (secret token excluded)."""
    svc = TriggerFlowService(db)
    flow = svc.get_flow(flow_id)
    if flow is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"TriggerFlow {flow_id} not found")
    return flow


@router.post(
    "/",
    response_model=TriggerFlowCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new trigger flow",
)
def create_trigger_flow(
    data: TriggerFlowCreate = Body(...),
    db: Session = Depends(get_db),
):
    """Create a new TriggerFlow.

    The response includes the **secret_token** in plaintext — this is the only time
    it will be visible.  Store it safely; use the *regenerate-token* endpoint to
    rotate it later.
    """
    svc = TriggerFlowService(db)
    try:
        flow, token = svc.create_flow(data)
    except Exception as exc:
        logger.error("Error creating TriggerFlow: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error creating TriggerFlow: {exc}",
        )

    # Build the response manually so we can inject the plaintext token
    resp = TriggerFlowCreateResponse.model_validate(flow)
    resp.secret_token = token
    return resp


@router.put(
    "/{flow_id}",
    response_model=TriggerFlowResponse,
    summary="Update a trigger flow",
)
def update_trigger_flow(
    flow_id: int = Path(..., description="TriggerFlow ID"),
    data: TriggerFlowUpdate = Body(...),
    db: Session = Depends(get_db),
):
    """Update name, description, enabled flag, and/or events.

    Passing ``events`` replaces the entire event list atomically.
    """
    svc = TriggerFlowService(db)
    if svc.get_flow(flow_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"TriggerFlow {flow_id} not found")
    updated = svc.update_flow(flow_id, data)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"TriggerFlow {flow_id} not found")
    return updated


@router.patch(
    "/{flow_id}/status",
    response_model=TriggerFlowResponse,
    summary="Enable or disable a trigger flow",
)
def toggle_trigger_flow_status(
    flow_id: int = Path(..., description="TriggerFlow ID"),
    enabled: bool = Body(..., embed=True, description="true to enable, false to disable"),
    db: Session = Depends(get_db),
):
    svc = TriggerFlowService(db)
    flow = svc.toggle_enabled(flow_id, enabled)
    if flow is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"TriggerFlow {flow_id} not found")
    return flow


@router.delete(
    "/{flow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a trigger flow",
)
def delete_trigger_flow(
    flow_id: int = Path(..., description="TriggerFlow ID"),
    db: Session = Depends(get_db),
):
    svc = TriggerFlowService(db)
    deleted = svc.delete_flow(flow_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"TriggerFlow {flow_id} not found")
    return None


@router.post(
    "/{flow_id}/regenerate-token",
    response_model=TriggerFlowCreateResponse,
    summary="Regenerate the secret token for a trigger flow",
)
def regenerate_token(
    flow_id: int = Path(..., description="TriggerFlow ID"),
    db: Session = Depends(get_db),
):
    """Generate a new secret token, invalidating the previous one immediately.

    The response includes the new plaintext token.
    """
    svc = TriggerFlowService(db)
    result = svc.regenerate_token(flow_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"TriggerFlow {flow_id} not found")
    flow, token = result
    resp = TriggerFlowCreateResponse.model_validate(flow)
    resp.secret_token = token
    return resp


# ---------------------------------------------------------------------------
# Fire endpoint
# ---------------------------------------------------------------------------

@router.post(
    "/{flow_id}/fire",
    summary="Fire a trigger flow via HTTP",
    responses={
        202: {"description": "Flow queued for background execution"},
        200: {"description": "Flow completed synchronously (wait=true)"},
        401: {"description": "Invalid or missing X-Trigger-Token"},
        403: {"description": "TriggerFlow is disabled"},
        404: {"description": "TriggerFlow not found"},
        500: {"description": "Execution error"},
    },
)
async def fire_trigger_flow(
    flow_id: int = Path(..., description="TriggerFlow ID"),
    wait: bool = Query(
        False,
        description="Block until the chain finishes. Useful for CI/CD pipelines.",
    ),
    wait_timeout_seconds: int = Query(
        120,
        ge=1,
        le=3600,
        description="Max seconds to wait when wait=true (default 120)",
    ),
    x_trigger_token: Optional[str] = Header(
        None,
        alias="X-Trigger-Token",
        description="Secret token returned when the flow was created",
    ),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
):
    """Fire a TriggerFlow by ID.

    **Authentication**: pass the secret token in the ``X-Trigger-Token`` header.

    **Sync vs async execution**:
    - Default (``wait=false``): returns ``202 Accepted`` immediately; the chain
      runs as a background task.
    - With ``wait=true``: blocks until the chain finishes (or ``wait_timeout_seconds``
      elapses) and returns the final status in the body.  Useful for CI/CD steps that
      need to know the outcome before continuing.
    """
    if not x_trigger_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Trigger-Token header",
        )

    svc = TriggerFlowService(db)

    # Validate token (also checks existence)
    flow = svc.verify_token(flow_id, x_trigger_token)
    if flow is None:
        # Could be wrong token or wrong ID — return 401 either way to avoid enumeration
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token or TriggerFlow not found",
        )

    if not flow.enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"TriggerFlow {flow_id} is disabled",
        )

    triggered_at = datetime.now(tz=timezone.utc).isoformat()
    events_count = (
        db.query(__import__("gluesync_scheduler.models.models", fromlist=["TriggerFlowEvent"]).TriggerFlowEvent)
        .filter_by(trigger_flow_id=flow_id)
        .count()
    )

    if wait:
        # Synchronous path: run the chain inline and wait up to wait_timeout_seconds
        try:
            success, err = await asyncio.wait_for(
                svc.fire(flow_id),
                timeout=float(wait_timeout_seconds),
            )
        except asyncio.TimeoutError:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content=FireResponse(
                    trigger_flow_id=flow_id,
                    triggered_at=triggered_at,
                    status=FireStatus.FAILED,
                    events_count=events_count,
                    message=f"Timed out after {wait_timeout_seconds}s waiting for chain to finish",
                ).model_dump(),
            )

        http_status = status.HTTP_200_OK
        fire_status = FireStatus.COMPLETED if success else FireStatus.FAILED
        msg = f"TriggerFlow '{flow.name}' completed" if success else f"TriggerFlow '{flow.name}' failed: {err}"
        return JSONResponse(
            status_code=http_status,
            content=FireResponse(
                trigger_flow_id=flow_id,
                triggered_at=triggered_at,
                status=fire_status,
                events_count=events_count,
                message=msg,
            ).model_dump(),
        )

    else:
        # Async path: queue the chain and return 202 immediately
        # We need a fresh DB session for the background task since the request session may close
        from gluesync_scheduler.db.database import SessionLocal

        async def _bg_fire(fid: int) -> None:
            bg_db = SessionLocal()
            try:
                bg_svc = TriggerFlowService(bg_db)
                await bg_svc.fire(fid)
            except Exception as exc:
                logger.error("Background fire of TriggerFlow %d failed: %s", fid, exc)
            finally:
                bg_db.close()

        asyncio.ensure_future(_bg_fire(flow_id))

        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=FireResponse(
                trigger_flow_id=flow_id,
                triggered_at=triggered_at,
                status=FireStatus.QUEUED,
                events_count=events_count,
                message=f"TriggerFlow '{flow.name}' queued for execution",
            ).model_dump(),
        )
