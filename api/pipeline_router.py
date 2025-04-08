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

from typing import List, Optional
from fastapi import APIRouter, HTTPException, status, Query, Path, Request, Depends, Body
from pydantic import BaseModel
import logging
from sqlalchemy.orm import Session

from database import get_db

from play_pause import PipelineManager
from schemas import ErrorResponse
from config import settings

# Configure logging
logger = logging.getLogger(__name__)

# Define response models
class OperationResponse(BaseModel):
    success: bool
    message: str
    details: Optional[dict] = None

# Security dependency to ensure requests only come from localhost
async def verify_localhost(request: Request):
    """
    Dependency to verify that the request is coming from localhost.
    This ensures that the pipeline endpoints can only be called from the local machine,
    which is important for security as these endpoints are used by cron jobs.
    
    Args:
        request: The FastAPI request object
        
    Raises:
        HTTPException: If the request is not from localhost
    """
    client_host = request.client.host if request.client else None
    
    # List of allowed hosts (localhost in various forms)
    allowed_hosts = ['localhost', '127.0.0.1', '::1']
    
    # Check if the request is from an allowed host
    if client_host not in allowed_hosts:
        logger.warning(f"Unauthorized access attempt to pipeline API from {client_host}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. This API endpoint can only be accessed from localhost."
        )
    
    # If we're in debug mode, log the access
    if settings.DEBUG:
        logger.debug(f"Localhost access to pipeline API from {client_host}")

# Create router
router = APIRouter(
    prefix="/pipelines",
    tags=["pipelines"],
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorResponse,
            "description": "Invalid request parameters"
        },
        status.HTTP_403_FORBIDDEN: {
            "model": ErrorResponse,
            "description": "Access denied. This API endpoint can only be accessed from localhost."
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "model": ErrorResponse,
            "description": "Internal server error during operation"
        },
    },
    dependencies=[Depends(verify_localhost)]  # Apply localhost verification to all routes
)

# Note: Router-level description is not supported in the installed FastAPI version
# The internal use only warning is included in each endpoint's documentation

@router.post(
    "/{pipeline_id}/play",
    response_model=dict,
    summary="Start pipeline or entities",
    description="[INTERNAL USE ONLY] Start a pipeline or specific entities within a pipeline. This endpoint is restricted to localhost access only."
)
async def play_pipeline(
    request: Request,
    pipeline_id: str = Path(..., description="The ID of the pipeline to start"),
    entity_ids: Optional[List[str]] = Query(None, description="Optional list of entity IDs to start. If not provided, all entities in the pipeline will be started."),
    with_snapshot: bool = Query(False, description="Whether to start with snapshot"),
    body: dict = Body(default=None)
):
    """
    Start a pipeline or specific entities within a pipeline.
    
    **INTERNAL USE ONLY**: This endpoint is exclusively for internal system use by the scheduler module's cron jobs.
    It is not intended for external API consumption and is restricted to localhost access only.
    
    ## Parameters
    - **pipeline_id**: The ID of the pipeline to start
    - **entity_ids**: Optional list of entity IDs to start. If not provided, all entities in the pipeline will be started.
    - **with_snapshot**: Whether to start with snapshot
    
    ## Returns
    A JSON object containing:
    - **success**: Whether the operation was successful
    - **message**: A message describing the result
    - **details**: Additional details about the operation (if any)
    
    ## Example Response
    ```json
    {
      "success": true,
      "message": "Pipeline started successfully",
      "details": {
        "pipeline_id": "pipeline-123",
        "entities_started": ["entity-456", "entity-789"]
      }
    }
    ```
    
    ## Errors
    - **400**: Invalid request parameters
    - **500**: Internal server error during operation
    - **403**: Forbidden if accessed from non-localhost source
    """
    try:
        manager = PipelineManager()
        
        # Check for entity_ids in both query parameters and request body
        entities_to_start = entity_ids or []
        
        # If we have a body with entity_ids, use those (overrides query parameters)
        if body and 'entity_ids' in body and isinstance(body['entity_ids'], list):
            entities_to_start = body['entity_ids']
            logger.info(f"Using entity_ids from request body: {entities_to_start}")
        
        # Check if with_snapshot is in the body
        snapshot_option = with_snapshot
        if body and 'with_snapshot' in body:
            snapshot_option = bool(body['with_snapshot'])
            logger.info(f"Using with_snapshot from request body: {snapshot_option}")
        
        if entities_to_start:
            # Start specific entities
            result = manager.play_entities(pipeline_id, entities_to_start, snapshot_option)
            return {
                "success": True,
                "message": f"Started {len(entities_to_start)} entities in pipeline {pipeline_id}",
                "details": {
                    "pipeline_id": pipeline_id,
                    "entities_started": entities_to_start,
                    "with_snapshot": snapshot_option
                }
            }
        else:
            # Start entire pipeline
            result = manager.play_pipeline(pipeline_id, snapshot_option)
            return {
                "success": True,
                "message": f"Started pipeline {pipeline_id}",
                "details": {
                    "pipeline_id": pipeline_id,
                    "with_snapshot": snapshot_option
                }
            }
        
    except Exception as e:
        logger.error(f"Error starting pipeline: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start pipeline: {str(e)}"
        )

@router.post(
    "/{pipeline_id}/pause",
    response_model=dict,
    summary="Stop pipeline or entities",
    description="[INTERNAL USE ONLY] Stop a pipeline or specific entities within a pipeline. This endpoint is restricted to localhost access only."
)
async def pause_pipeline(
    request: Request,
    pipeline_id: str = Path(..., description="The ID of the pipeline to stop"),
    entity_ids: Optional[List[str]] = Query(None, description="Optional list of entity IDs to stop. If not provided, all entities in the pipeline will be stopped."),
    body: dict = Body(default=None)
):
    """
    Stop a pipeline or specific entities within a pipeline.
    
    **INTERNAL USE ONLY**: This endpoint is exclusively for internal system use by the scheduler module's cron jobs.
    It is not intended for external API consumption and is restricted to localhost access only.
    
    ## Parameters
    - **pipeline_id**: The ID of the pipeline to stop
    - **entity_ids**: Optional list of entity IDs to stop. If not provided, all entities in the pipeline will be stopped.
    
    ## Returns
    A JSON object containing:
    - **success**: Whether the operation was successful
    - **message**: A message describing the result
    - **details**: Additional details about the operation (if any)
    
    ## Example Response
    ```json
    {
      "success": true,
      "message": "Pipeline stopped successfully",
      "details": {
        "pipeline_id": "pipeline-123",
        "entities_stopped": ["entity-456", "entity-789"]
      }
    }
    ```
    
    ## Errors
    - **400**: Invalid request parameters
    - **500**: Internal server error during operation
    - **403**: Forbidden if accessed from non-localhost source
    """
    try:
        manager = PipelineManager()
        
        # Check for entity_ids in both query parameters and request body
        entities_to_stop = entity_ids or []
        
        # If we have a body with entity_ids, use those (overrides query parameters)
        if body and 'entity_ids' in body and isinstance(body['entity_ids'], list):
            entities_to_stop = body['entity_ids']
            logger.info(f"Using entity_ids from request body: {entities_to_stop}")
        
        if entities_to_stop:
            # Stop specific entities
            result = manager.pause_entities(pipeline_id, entities_to_stop)
            return {
                "success": True,
                "message": f"Stopped {len(entities_to_stop)} entities in pipeline {pipeline_id}",
                "details": {
                    "pipeline_id": pipeline_id,
                    "entities_stopped": entities_to_stop
                }
            }
        else:
            # Stop entire pipeline
            result = manager.pause_pipeline(pipeline_id)
            return {
                "success": True,
                "message": f"Stopped pipeline {pipeline_id}",
                "details": {
                    "pipeline_id": pipeline_id
                }
            }
    except Exception as e:
        logger.error(f"Error stopping pipeline: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to stop pipeline: {str(e)}"
        )

@router.post(
    "/{pipeline_id}/resync",
    response_model=dict,
    summary="Create snapshot for pipeline or entities",
    description="[INTERNAL USE ONLY] Create a data snapshot for a pipeline or specific entities within a pipeline. This endpoint is restricted to localhost access only."
)
async def resync_pipeline(
    request: Request,
    pipeline_id: str = Path(..., description="The ID of the pipeline to resync"),
    entity_ids: Optional[List[str]] = Query(None, description="Optional list of entity IDs to resync. If not provided, all entities in the pipeline will be resynced."),
    snapshot_write_method: str = Query("UPSERT", description="The write method for the snapshot, default is UPSERT."),
    body: dict = Body(default=None)
):
    """
    Create a data snapshot for a pipeline or specific entities within a pipeline.
    
    **INTERNAL USE ONLY**: This endpoint is exclusively for internal system use by the scheduler module's cron jobs.
    It is not intended for external API consumption and is restricted to localhost access only.
    
    ## Parameters
    - **pipeline_id**: The ID of the pipeline to resync
    - **entity_ids**: Optional list of entity IDs to resync. If not provided, all entities in the pipeline will be resynced.
    - **snapshot_write_method**: The write method for the snapshot, default is UPSERT.
    
    ## Returns
    A JSON object containing:
    - **success**: Whether the operation was successful
    - **message**: A message describing the result
    - **details**: Additional details about the operation (if any)
    
    ## Example Response
    ```json
    {
      "success": true,
      "message": "Pipeline one-time snapshot triggered successfully",
      "details": {
        "pipeline_id": "pipeline-123",
        "entities_resynced": ["entity-456", "entity-789"],
        "snapshot_write_method": "UPSERT"
      }
    }
    ```
    
    ## Errors
    - **400**: Invalid request parameters
    - **500**: Internal server error during operation
    - **403**: Forbidden if accessed from non-localhost source
    """
    try:
        manager = PipelineManager()
        
        # Check for entity_ids in both query parameters and request body
        entities_to_resync = entity_ids or []
        
        # If we have a body with entity_ids, use those (overrides query parameters)
        if body and 'entity_ids' in body and isinstance(body['entity_ids'], list):
            entities_to_resync = body['entity_ids']
            logger.info(f"Using entity_ids from request body: {entities_to_resync}")
        
        # Check if snapshot_write_method is in the body
        write_method = snapshot_write_method
        if body and 'snapshot_write_method' in body:
            write_method = body['snapshot_write_method']
            logger.info(f"Using snapshot_write_method from request body: {write_method}")
        
        if entities_to_resync:
            # Resync specific entities
            result = manager.resync_entities(pipeline_id, entities_to_resync, write_method)
            return {
                "success": True,
                "message": f"Triggered one-time snapshot for {len(entities_to_resync)} entities in pipeline {pipeline_id}",
                "details": {
                    "pipeline_id": pipeline_id,
                    "entities_resynced": entities_to_resync,
                    "snapshot_write_method": write_method
                }
            }
        else:
            # Resync entire pipeline
            result = manager.resync_pipeline(pipeline_id, write_method)
            return {
                "success": True,
                "message": f"Triggered one-time snapshot for pipeline {pipeline_id}",
                "details": {
                    "pipeline_id": pipeline_id,
                    "snapshot_write_method": write_method
                }
            }
        
    except Exception as e:
        logger.error(f"Error resyncing pipeline: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to resync pipeline: {str(e)}"
        )
