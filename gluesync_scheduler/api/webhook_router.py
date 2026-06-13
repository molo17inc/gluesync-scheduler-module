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
    x_task_guid: Optional[str] = Header(None, alias="X-Task-GUID"),
    ext_module: Optional[str] = Header(None, alias="EXT_MODULE"),
) -> JSONResponse:
    """Endpoint called by the Gluesync corehub when a sync chained event completes.

    Required headers
    ----------------
    ``EXT_MODULE``
        Must be ``chronos``.  Identifies the module that registered the webhook.
    ``X-Task-GUID``
        UUID registered when the sync chained event was set up.

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

    # Validate task GUID
    if not x_task_guid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing X-Task-GUID header",
        )

    # Optionally parse body for audit/logging
    user: Optional[str] = None
    try:
        body: Dict[str, Any] = await request.json()
        user = body.get("user")
    except Exception:
        pass  # body is optional

    logger.info(
        "webhook_notify: received callback guid=%s user=%s",
        x_task_guid,
        user or "<not provided>",
    )

    found = chain_execution_service.notify_webhook_received(x_task_guid)
    if not found:
        # Not an error — the webhook may have already timed out.
        logger.warning("webhook_notify: no pending chain found for guid=%s", x_task_guid)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"acknowledged": False, "reason": "no pending chain for this guid"},
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"acknowledged": True, "guid": x_task_guid},
    )
