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

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, Path, Body
from sqlalchemy.orm import Session

from database import get_db
from models import TaskType
from schemas import JobCreate, JobUpdate, Job, JobList, ErrorResponse
from services.job_service import JobService

router = APIRouter(
    prefix="/jobs",
    tags=["jobs"],
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "The requested resource was not found"
        },
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorResponse,
            "description": "Invalid request parameters or payload"
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Resource conflict, e.g., duplicate cron job identifier"
        },
    },
)

@router.get("/", response_model=JobList, summary="List all scheduled jobs", description="Retrieve a paginated list of all scheduled jobs with optional filtering by task type and enabled status")
def list_jobs(
    skip: int = Query(0, ge=0, description="Number of records to skip for pagination"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of records to return"),
    task_type: Optional[TaskType] = Query(None, description="Filter by task type (use lowercase values in API requests):\n- entity_start: Start a specific entity within a pipeline\n- entity_stop: Stop a specific entity within a pipeline\n- pipeline_start: Start all entities in a pipeline\n- pipeline_stop: Stop all entities in a pipeline\n- entity_snapshot: Create a data snapshot of a specific entity\n- pipeline_snapshot: Create a data snapshot of all entities in a pipeline"),
    enabled: Optional[bool] = Query(None, description="Filter by enabled status (true/false)"),
    db: Session = Depends(get_db)
):
    """
    Get a list of all scheduled jobs with optional filtering.
    
    ## Parameters
    - **skip**: Number of records to skip (for pagination)
    - **limit**: Maximum number of records to return (for pagination)
    - **task_type**: Filter jobs by task type
    - **enabled**: Filter jobs by enabled status
    
    ## Returns
    A JSON object containing:
    - **items**: List of scheduled job objects
    - **total**: Total count of jobs (without pagination)
    
    ## Example
    ```json
    {
      "items": [
        {
          "id": 1,
          "name": "Daily entity backup",
          "description": "Create a daily snapshot of critical entities",
          "task_type": "ENTITY_SNAPSHOT",
          "cron_expression": "0 0 * * *",
          "pipeline_id": "pipeline-123",
          "entity_id": "entity-456",
          "with_snapshot": true,
          "enabled": true,
          "created_at": "2025-03-20T10:00:00Z",
          "updated_at": "2025-03-20T10:00:00Z",
          "last_run": "2025-03-20T00:00:00Z",
          "next_run": "2025-03-21T00:00:00Z"
        }
      ],
      "total": 1
    }
    ```
    """
    job_service = JobService(db)
    jobs = job_service.get_jobs(skip=skip, limit=limit)
    
    # Apply filters if provided
    if task_type:
        jobs = [job for job in jobs if job.task_type == task_type]
    
    if enabled is not None:
        jobs = [job for job in jobs if job.enabled == enabled]
    
    return {"items": jobs, "total": job_service.count_jobs()}

@router.get("/{job_id}", response_model=Job, summary="Get a specific job", description="Retrieve detailed information about a specific scheduled job by its ID")
def get_job(job_id: int = Path(..., description="The ID of the scheduled job to retrieve"), db: Session = Depends(get_db)):
    """
    Get a specific scheduled job by ID.
    
    ## Parameters
    - **job_id**: The unique identifier of the job to retrieve
    
    ## Returns
    A complete job object with all details
    
    ## Example Response
    ```json
    {
      "id": 1,
      "name": "Daily entity backup",
      "description": "Create a daily snapshot of critical entities",
      "task_type": "ENTITY_SNAPSHOT",
      "cron_expression": "0 0 * * *",
      "pipeline_id": "pipeline-123",
      "entity_id": "entity-456",
      "with_snapshot": true,
      "enabled": true,
      "command": "python play_pause.py resync --pipeline pipeline-123 --entity entity-456",
      "cron_job_identifier": "gluesync_job_1",
      "created_at": "2025-03-20T10:00:00Z",
      "updated_at": "2025-03-20T10:00:00Z",
      "last_run": "2025-03-20T00:00:00Z",
      "next_run": "2025-03-21T00:00:00Z"
    }
    ```
    
    ## Errors
    - **404**: Job with the specified ID was not found
    """
    job_service = JobService(db)
    job = job_service.get_job_by_id(job_id)
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID {job_id} not found"
        )
    
    return job

@router.post("/", response_model=Job, status_code=status.HTTP_201_CREATED, summary="Create a new job", description="Create a new scheduled job with the specified parameters")
def create_job(job_data: JobCreate = Body(..., description="Job data to create", example={
    "name": "Monday-Wednesday-Friday Job",
    "description": "Runs on specific days at 8:30 AM",
    "task_type": "ENTITY_SNAPSHOT",
    "schedule": {
        "days_of_week": ["monday", "wednesday", "friday"],
        "hour": 8,
        "minute": 30
    },
    "pipeline_id": "pipeline-123",
    "entity_id": "entity-456",
    "with_snapshot": True,
    "enabled": True
}), db: Session = Depends(get_db)):
    """
    Create a new scheduled job.
    
    ## Request Body
    - **name**: Name of the job (required)
    - **description**: Description of the job (optional)
    - **task_type**: Type of task to perform (required)
    - **schedule**: User-friendly schedule configuration (optional, but either schedule or cron_expression must be provided)
      - **days_of_week**: Array of days when the job should run (e.g., ["monday", "wednesday", "friday"])
      - **hour**: Hour of the day (0-23)
      - **minute**: Minute of the hour (0-59)
    - **cron_expression**: Cron expression for scheduling (optional if schedule is provided)
    - **pipeline_id**: ID of the pipeline to operate on (required)
    - **entity_id**: ID of the entity to operate on (optional, required for entity operations)
    - **with_snapshot**: Whether to include snapshot (optional, default: false)
    - **enabled**: Whether the job is enabled (optional, default: true)
    
    ## Returns
    The created job object with all details including the generated ID
    
    ## Example Request
    ```json
    {
      "name": "Monday-Wednesday-Friday Job",
      "description": "Runs on specific days at 8:30 AM",
      "task_type": "ENTITY_SNAPSHOT",
      "schedule": {
        "days_of_week": ["monday", "wednesday", "friday"],
        "hour": 8,
        "minute": 30
      },
      "pipeline_id": "pipeline-123",
      "entity_id": "entity-456",
      "with_snapshot": true,
      "enabled": true
    }
    ```
    
    ## Alternative Example with Cron Expression
    ```json
    {
      "name": "Daily entity backup",
      "description": "Create a daily snapshot of critical entities",
      "task_type": "ENTITY_SNAPSHOT",
      "cron_expression": "0 0 * * *",
      "pipeline_id": "pipeline-123",
      "entity_id": "entity-456",
      "with_snapshot": true,
      "enabled": true
    }
    ```
    
    ## Errors
    - **400**: Invalid request data
    - **409**: Conflict with existing job (e.g., duplicate cron job identifier)
    - **500**: Server error during job creation
    """
    job_service = JobService(db)
    
    try:
        return job_service.create_job(job_data)
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create job: {str(e)}"
        )

@router.put("/{job_id}", response_model=Job, summary="Update an existing job", description="Update an existing scheduled job with the specified parameters")
def update_job(job_id: int = Path(..., description="The ID of the job to update"), 
              job_data: JobUpdate = Body(..., description="Job data to update", example={
                  "name": "Updated job schedule",
                  "description": "Now runs on weekends at midnight",
                  "schedule": {
                      "days_of_week": ["saturday", "sunday"],
                      "hour": 0,
                      "minute": 0
                  },
                  "enabled": True
              }), 
              db: Session = Depends(get_db)):
    """
    Update an existing scheduled job.
    
    ## Parameters
    - **job_id**: The unique identifier of the job to update
    
    ## Request Body
    All fields are optional. Only specified fields will be updated:
    - **name**: Updated name of the job
    - **description**: Updated description of the job
    - **schedule**: Updated user-friendly schedule configuration
      - **days_of_week**: Array of days when the job should run (e.g., ["monday", "wednesday", "friday"])
      - **hour**: Hour of the day (0-23)
      - **minute**: Minute of the hour (0-59)
    - **cron_expression**: Updated cron expression for scheduling (not required if schedule is provided)
    - **with_snapshot**: Updated snapshot setting
    - **enabled**: Updated enabled status
    
    ## Returns
    The updated job object with all details
    
    ## Example Request
    ```json
    {
      "name": "Updated job schedule",
      "description": "Now runs on weekends at midnight",
      "schedule": {
        "days_of_week": ["saturday", "sunday"],
        "hour": 0,
        "minute": 0
      },
      "enabled": true
    }
    ```
    
    ## Alternative Example with Cron Expression
    ```json
    {
      "name": "Updated daily entity backup",
      "description": "Updated description",
      "cron_expression": "0 0 * * *",
      "enabled": true
    }
    ```
    
    ## Errors
    - **400**: Invalid request data
    - **404**: Job with the specified ID was not found
    - **409**: Conflict with existing job (e.g., duplicate cron job identifier)
    - **500**: Server error during job update
    """
    job_service = JobService(db)
    
    try:
        updated_job = job_service.update_job(job_id, job_data)
        
        if not updated_job:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job with ID {job_id} not found"
            )
        
        return updated_job
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update job: {str(e)}"
        )

@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a job", description="Delete a scheduled job by its ID")
def delete_job(job_id: int = Path(..., description="The ID of the job to delete"), db: Session = Depends(get_db)):
    """
    Delete a scheduled job.
    
    ## Parameters
    - **job_id**: The unique identifier of the job to delete
    
    ## Returns
    No content (204) on successful deletion
    
    ## Errors
    - **404**: Job with the specified ID was not found
    - **500**: Server error during job deletion
    """
    job_service = JobService(db)
    
    if not job_service.delete_job(job_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID {job_id} not found"
        )
