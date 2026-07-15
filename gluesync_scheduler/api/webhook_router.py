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

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from gluesync_scheduler.services.chain_execution_service import chain_execution_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/webhooks",
    tags=["webhooks"],
)

_EXPECTED_MODULE = "chronos"


@router.post(
    "/notify",
    summary="Receive a corehub webhook callback for a sync chained event",
    response_description="Acknowledged",
    status_code=status.HTTP_200_OK,
)
async def webhook_notify(
    request: Request,
    x_event_id: Optional[str] = Header(None, alias="X-Event-ID"),
    ext_module: Optional[str] = Header(None, alias="EXT_MODULE"),
) -> JSONResponse:
    """Endpoint called by the Gluesync corehub when a sync chained event completes.

    Required headers
    ----------------
    ``EXT_MODULE``
        Must be ``chronos``.  Identifies the module that registered the webhook.
    ``X-Event-ID``
        The chained event ID registered when the persistent webhook was set up.

    Optional body
    -------------
    Any valid JSON.  If a ``user`` key is present it is logged for audit purposes.
    """
    # Validate module identifier
    if not ext_module or ext_module.strip() != _EXPECTED_MODULE:
        logger.warning(
            "webhook_notify: rejected request — EXT_MODULE='%s' (expected '%s')",
            ext_module,
            _EXPECTED_MODULE,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid or missing EXT_MODULE header (expected '{_EXPECTED_MODULE}')",
        )

    # Validate event ID
    if not x_event_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-Event-ID header",
        )

    # Optionally parse body for audit/logging
    user: Optional[str] = None
    try:
        body: Dict[str, Any] = await request.json()
        user = body.get("user")
    except Exception:
        pass  # body is optional

    logger.info(
        "webhook_notify: received callback event_id=%s user=%s",
        x_event_id,
        user or "<not provided>",
    )

    found = chain_execution_service.notify_webhook_received(x_event_id)
    if not found:
        # Not an error — the callback arrived outside the active listening window
        # (e.g. a manual pipeline action between scheduled runs) or the chain
        # already timed out.  Either way, we acknowledge so CoreHub doesn't retry.
        logger.info("webhook_notify: no active listener for event_id=%s (outside scheduled window)", x_event_id)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"acknowledged": False, "reason": "no active listener for this event_id (outside scheduled window)"},
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"acknowledged": True, "event_id": x_event_id},
    )


@router.post(
    "/platform-event",
    summary="Receive a corehub webhook callback for a platform event trigger flow",
    response_description="Acknowledged",
    status_code=status.HTTP_200_OK,
)
async def platform_event_notify(
    request: Request,
    x_trigger_flow_id: Optional[str] = Header(None, alias="X-Trigger-Flow-ID"),
    ext_module: Optional[str] = Header(None, alias="EXT_MODULE"),
) -> JSONResponse:
    """Endpoint called by the Gluesync corehub when a platform event fires.

    Required headers
    ----------------
    ``EXT_MODULE``
        Must be ``chronos``.
    ``X-Trigger-Flow-ID``
        The trigger flow ID to fire.
    """
    if not ext_module or ext_module.strip() != _EXPECTED_MODULE:
        logger.warning(
            "platform_event_notify: rejected request — EXT_MODULE='%s' (expected '%s')",
            ext_module,
            _EXPECTED_MODULE,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid or missing EXT_MODULE header (expected '{_EXPECTED_MODULE}')",
        )

    if not x_trigger_flow_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-Trigger-Flow-ID header",
        )

    try:
        flow_id = int(x_trigger_flow_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid X-Trigger-Flow-ID header: {x_trigger_flow_id}",
        )

    logger.info("platform_event_notify: received callback for flow_id=%d", flow_id)

    # Fire the trigger flow in the background
    import asyncio
    from gluesync_scheduler.db.database import SessionLocal
    from gluesync_scheduler.services.trigger_flow_service import TriggerFlowService

    async def _fire_flow():
        db = SessionLocal()
        try:
            svc = TriggerFlowService(db)
            flow = svc.get_flow(flow_id)
            if flow is None:
                logger.warning("platform_event_notify: flow %d not found", flow_id)
                return
            if not flow.enabled:
                logger.info("platform_event_notify: flow %d is disabled — skipping", flow_id)
                return
            success, err = await svc.fire(flow_id, source="platform_event")
            if not success:
                logger.error("platform_event_notify: flow %d execution failed: %s", flow_id, err)
        finally:
            db.close()

    asyncio.ensure_future(_fire_flow())

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"acknowledged": True, "flow_id": flow_id},
    )
