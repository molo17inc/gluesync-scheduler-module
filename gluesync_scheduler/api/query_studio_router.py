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
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from gluesync_scheduler.core.play_pause import CoreHubClient
from gluesync_scheduler.models.schemas import ErrorResponse
from gluesync_scheduler.security import CurrentUser, current_user

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/query-studio",
    tags=["query-studio"],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "Unauthenticated",
        },
        status.HTTP_502_BAD_GATEWAY: {
            "model": ErrorResponse,
            "description": "CoreHub request failed",
        },
    },
)


@router.get("/agents", summary="List Query Studio agents from CoreHub")
async def list_query_studio_agents(
    _user: Annotated[CurrentUser, Depends(current_user)],
):
    """Proxy CoreHub ``GET /query-studio/agents`` for the Scheduler UI agent picker.

    Returns the Hub payload as-is so the Control Plane can populate the picker
    without going through Connect.
    """
    try:
        client = CoreHubClient()
        payload = client.list_query_studio_agents()
    except Exception:
        logger.exception("Failed to list Query Studio agents from CoreHub")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to list Query Studio agents from CoreHub",
        )

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="CoreHub did not return Query Studio agents",
        )
    if isinstance(payload, dict) and payload.get("status") == "error":
        raise HTTPException(
            status_code=payload.get("status_code") or status.HTTP_502_BAD_GATEWAY,
            detail=payload.get("message") or "CoreHub Query Studio agents request failed",
        )
    return JSONResponse(content=payload)
