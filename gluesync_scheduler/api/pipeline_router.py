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

from gluesync_scheduler.db.database import get_db
from gluesync_scheduler.core.play_pause import PipelineManager
from gluesync_scheduler.models.schemas import ErrorResponse, OperationResponse
from gluesync_scheduler.config.settings import settings

# Configure logging
logger = logging.getLogger(__name__)

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
    client_host = request.client.host
    
    # Check if the request is coming from localhost
    if client_host not in ['localhost', '127.0.0.1', '::1']:
        # Check if we're running in Docker where the request might come from the Docker network
        if not (client_host.startswith('172.') or client_host.startswith('192.168.')):
            logger.warning(f"Unauthorized access attempt from {client_host}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This endpoint can only be accessed from localhost"
            )
    
    # Extract cron_job_identifier from headers or query parameters if present
    cron_job_identifier = request.headers.get('X-Cron-Job-Identifier')
    if not cron_job_identifier:
        # Try to get from query parameters
        cron_job_identifier = request.query_params.get('cron_job_identifier')
    
    # Add to request state for use in endpoint handlers
    request.state.cron_job_identifier = cron_job_identifier

# Create router
router = APIRouter(
    prefix="/pipelines",
    tags=["pipelines"],
    dependencies=[Depends(verify_localhost)],
    responses={
        status.HTTP_403_FORBIDDEN: {
            "model": ErrorResponse,
            "description": "Forbidden: This endpoint can only be accessed from localhost"
        },
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorResponse,
            "description": "Invalid request data"
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "model": ErrorResponse,
            "description": "Server error"
        }
    }
)

@router.post("/{pipeline_id}/play", response_model=OperationResponse, summary="Start a pipeline or entities")
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
    # Get cron_job_identifier from request state
    cron_job_identifier = getattr(request.state, 'cron_job_identifier', None)
    
    # Extract entity_ids from body if provided
    if body and 'entity_ids' in body and body['entity_ids']:
        entity_ids = body['entity_ids']
    
    # Extract with_snapshot from body if provided
    if body and 'with_snapshot' in body:
        with_snapshot = body['with_snapshot']
    
    logger.info(f"Received play request for pipeline {pipeline_id}")
    if entity_ids:
        logger.info(f"Entity IDs: {entity_ids}")
    logger.info(f"With snapshot: {with_snapshot}")
    if cron_job_identifier:
        logger.info(f"Cron job identifier: {cron_job_identifier}")
    
    try:
        # Initialize pipeline manager
        pipeline_manager = PipelineManager()
        
        # Start pipeline or entities
        if entity_ids:
            # Start specific entities
            result = await pipeline_manager.play_entities(pipeline_id, entity_ids, with_snapshot)
            message = f"Entities started successfully in pipeline {pipeline_id}"
            details = {
                "pipeline_id": pipeline_id,
                "entities_started": entity_ids,
                "with_snapshot": with_snapshot
            }
        else:
            # Start entire pipeline
            result = await pipeline_manager.play_pipeline(pipeline_id, with_snapshot)
            message = f"Pipeline {pipeline_id} started successfully"
            details = {
                "pipeline_id": pipeline_id,
                "with_snapshot": with_snapshot
            }
        
        # Update job status if cron_job_identifier is provided
        if cron_job_identifier:
            from gluesync_scheduler.db.database import SessionLocal
            from gluesync_scheduler.cli.job_runner import update_job_status
            
            try:
                update_job_status(cron_job_identifier, True)
                logger.info(f"Updated job status for {cron_job_identifier}")
            except Exception as e:
                logger.error(f"Error updating job status: {str(e)}")
        
        # Return a simplified response to avoid recursion issues
        return {
            "success": True,
            "message": message,
            "data": None  # Use None instead of complex details object
        }
    except Exception as e:
        logger.error(f"Error starting pipeline: {str(e)}")
        
        # Update job status with error if cron_job_identifier is provided
        if cron_job_identifier:
            try:
                from gluesync_scheduler.cli.job_runner import update_job_status
                update_job_status(cron_job_identifier, False, str(e))
                logger.info(f"Updated job status with error for {cron_job_identifier}")
            except Exception as update_error:
                logger.error(f"Error updating job status: {str(update_error)}")
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error starting pipeline: {str(e)}"
        )

@router.post("/{pipeline_id}/pause", response_model=OperationResponse, summary="Stop a pipeline or entities")
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
    # Get cron_job_identifier from request state
    cron_job_identifier = getattr(request.state, 'cron_job_identifier', None)
    
    # Extract entity_ids from body if provided
    if body and 'entity_ids' in body and body['entity_ids']:
        entity_ids = body['entity_ids']
    
    logger.info(f"Received pause request for pipeline {pipeline_id}")
    if entity_ids:
        logger.info(f"Entity IDs: {entity_ids}")
    if cron_job_identifier:
        logger.info(f"Cron job identifier: {cron_job_identifier}")
    
    try:
        # Initialize pipeline manager
        pipeline_manager = PipelineManager()
        
        # Stop pipeline or entities
        if entity_ids:
            # Stop specific entities
            result = await pipeline_manager.pause_entities(pipeline_id, entity_ids)
            message = f"Entities stopped successfully in pipeline {pipeline_id}"
            details = {
                "pipeline_id": pipeline_id,
                "entities_stopped": entity_ids
            }
        else:
            # Stop entire pipeline
            result = await pipeline_manager.pause_pipeline(pipeline_id)
            message = f"Pipeline {pipeline_id} stopped successfully"
            details = {
                "pipeline_id": pipeline_id
            }
        
        # Update job status if cron_job_identifier is provided
        if cron_job_identifier:
            from gluesync_scheduler.cli.job_runner import update_job_status
            
            try:
                update_job_status(cron_job_identifier, True)
                logger.info(f"Updated job status for {cron_job_identifier}")
            except Exception as e:
                logger.error(f"Error updating job status: {str(e)}")
        
        # Return a simplified response to avoid recursion issues
        return {
            "success": True,
            "message": message,
            "data": None  # Use None instead of complex details object
        }
    except Exception as e:
        logger.error(f"Error stopping pipeline: {str(e)}")
        
        # Update job status with error if cron_job_identifier is provided
        if cron_job_identifier:
            try:
                from gluesync_scheduler.cli.job_runner import update_job_status
                update_job_status(cron_job_identifier, False, str(e))
                logger.info(f"Updated job status with error for {cron_job_identifier}")
            except Exception as update_error:
                logger.error(f"Error updating job status: {str(update_error)}")
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error stopping pipeline: {str(e)}"
        )

@router.post("/{pipeline_id}/resync", response_model=OperationResponse, summary="Create a data snapshot for a pipeline or entities")
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
    # Get cron_job_identifier from request state
    cron_job_identifier = getattr(request.state, 'cron_job_identifier', None)
    
    # Extract entity_ids from body if provided
    if body and 'entity_ids' in body and body['entity_ids']:
        entity_ids = body['entity_ids']
    
    # Extract snapshot_write_method from body if provided
    if body and 'snapshot_write_method' in body:
        snapshot_write_method = body['snapshot_write_method']
    
    logger.info(f"Received resync request for pipeline {pipeline_id}")
    if entity_ids:
        logger.info(f"Entity IDs: {entity_ids}")
    logger.info(f"Snapshot write method: {snapshot_write_method}")
    if cron_job_identifier:
        logger.info(f"Cron job identifier: {cron_job_identifier}")
    
    try:
        # Initialize pipeline manager
        pipeline_manager = PipelineManager()
        
        # Resync pipeline or entities
        if entity_ids:
            # Resync specific entities
            result = await pipeline_manager.resync_entities(pipeline_id, entity_ids, snapshot_write_method)
            message = f"One-time snapshot triggered successfully for entities in pipeline {pipeline_id}"
            details = {
                "pipeline_id": pipeline_id,
                "entities_resynced": entity_ids,
                "snapshot_write_method": snapshot_write_method
            }
        else:
            # Resync entire pipeline
            result = await pipeline_manager.resync_pipeline(pipeline_id, snapshot_write_method)
            message = f"One-time snapshot triggered successfully for pipeline {pipeline_id}"
            details = {
                "pipeline_id": pipeline_id,
                "snapshot_write_method": snapshot_write_method
            }
        
        # Update job status if cron_job_identifier is provided
        if cron_job_identifier:
            from gluesync_scheduler.cli.job_runner import update_job_status
            
            try:
                update_job_status(cron_job_identifier, True)
                logger.info(f"Updated job status for {cron_job_identifier}")
            except Exception as e:
                logger.error(f"Error updating job status: {str(e)}")
        
        # Return a simplified response to avoid recursion issues
        return {
            "success": True,
            "message": message,
            "data": None  # Use None instead of complex details object
        }
    except Exception as e:
        logger.error(f"Error resyncing pipeline: {str(e)}")
        
        # Update job status with error if cron_job_identifier is provided
        if cron_job_identifier:
            try:
                from gluesync_scheduler.cli.job_runner import update_job_status
                update_job_status(cron_job_identifier, False, str(e))
                logger.info(f"Updated job status with error for {cron_job_identifier}")
            except Exception as update_error:
                logger.error(f"Error updating job status: {str(update_error)}")
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error resyncing pipeline: {str(e)}"
        )
