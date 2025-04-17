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

from gluesync_scheduler.db.database import get_db
from gluesync_scheduler.models.models import TaskType
from gluesync_scheduler.models.schemas import JobCreate, JobUpdate, Job, JobList, ErrorResponse
from gluesync_scheduler.services.job_service import JobService

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
            "description": "Invalid request data"
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "model": ErrorResponse,
            "description": "Server error"
        }
    }
)

@router.get("/", response_model=JobList, summary="List all scheduled jobs")
async def list_jobs(
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
          "entity_ids": ["entity-456", "entity-789"],
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
    jobs, total = job_service.get_jobs(skip=skip, limit=limit, task_type=task_type, enabled=enabled)
    return {"items": jobs, "total": total}


@router.get("/{job_id}", response_model=Job, responses={
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Job not found"}
}, summary="Get a specific scheduled job")
async def get_job(job_id: int = Path(..., description="The ID of the scheduled job to retrieve"), db: Session = Depends(get_db)):
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
      "entity_ids": ["entity-456", "entity-789"],
      "with_snapshot": true,
      "enabled": true,
      "command": "python3 play_pause.py resync --pipeline pipeline-123 --entity entity-456,entity-789",
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
    job = job_service.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID {job_id} not found"
        )
    return job


@router.post("/", response_model=Job, status_code=status.HTTP_201_CREATED, responses={
    status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse, "description": "Invalid request data"},
    status.HTTP_409_CONFLICT: {"model": ErrorResponse, "description": "Conflict with existing job"},
    status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse, "description": "Server error during job creation"}
}, summary="Create a new scheduled job")
async def create_job(job_data: JobCreate = Body(..., description="Job data to create", example={
    "name": "Monday-Wednesday-Friday Job",
    "description": "Runs on specific days at 8:30 AM",
    "task_type": "ENTITY_SNAPSHOT",
    "schedule": {
        "days_of_week": ["monday", "wednesday", "friday"],
        "hour": 8,
        "minute": 30
    },
    "pipeline_id": "pipeline-123",
    "entity_ids": ["entity-456", "entity-789"],
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
    - **entity_ids**: List of entity IDs to operate on (optional, required for entity operations)
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
      "entity_ids": ["entity-456", "entity-789"],
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
      "entity_ids": ["entity-456", "entity-789"],
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
        job = job_service.create_job(job_data)
        return job
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A job with the same identifier already exists: {str(e)}"
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error creating job: {str(e)}"
            )


@router.put("/{job_id}", response_model=Job, responses={
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Job not found"},
    status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse, "description": "Invalid request data"},
    status.HTTP_409_CONFLICT: {"model": ErrorResponse, "description": "Conflict with existing job"},
    status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse, "description": "Server error during job update"}
}, summary="Update an existing scheduled job")
async def update_job(job_id: int = Path(..., description="The ID of the job to update"), 
              job_data: JobUpdate = Body(..., description="Job data to update", example={
                  "name": "Updated job schedule",
                  "description": "Now runs on weekends at midnight",
                  "task_type": "pipeline_stop",
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
    - **task_type**: Updated type of task to perform (e.g., "pipeline_start", "pipeline_stop", "entity_snapshot")
    - **schedule**: Updated user-friendly schedule configuration
      - **days_of_week**: Array of days when the job should run (e.g., ["monday", "wednesday", "friday"])
      - **hour**: Hour of the day (0-23)
      - **minute**: Minute of the hour (0-59)
    - **cron_expression**: Updated cron expression for scheduling (not required if schedule is provided)
    - **pipeline_id**: Updated pipeline ID
    - **entity_ids**: Updated list of entity IDs to operate on
    - **with_snapshot**: Updated snapshot setting
    - **enabled**: Updated enabled status
    
    ## Returns
    The updated job object with all details
    
    ## Example Request
    ```json
    {
      "name": "Updated job schedule",
      "description": "Now runs on weekends at midnight",
      "task_type": "pipeline_stop",
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
    
    # Check if job exists
    existing_job = job_service.get_job(job_id)
    if not existing_job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID {job_id} not found"
        )
    
    try:
        updated_job = job_service.update_job(job_id, job_data)
        return updated_job
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A job with the same identifier already exists: {str(e)}"
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error updating job: {str(e)}"
            )


@router.post("/{job_id}/run", responses={
    status.HTTP_200_OK: {
        "description": "Job execution result",
        "content": {
            "application/json": {
                "example": {
                    "success": True,
                    "message": "Job executed successfully",
                    "job_id": 1,
                    "exit_code": 0,
                    "stdout": "Pipeline started successfully",
                    "stderr": ""
                }
            }
        }
    },
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Job not found"
    },
    status.HTTP_500_INTERNAL_SERVER_ERROR: {
        "model": ErrorResponse,
        "description": "Error running job"
    }
})
def run_job(
    job_id: int = Path(..., description="The ID of the job to run"),
    db: Session = Depends(get_db)
):
    """
    Run a job.
    
    ## Parameters
    - **job_id**: The unique identifier of the job to run
    
    ## Returns
    A dictionary with the execution results:
    - **success**: Whether the job executed successfully
    - **message**: A message describing the result
    - **job_id**: The ID of the job that was run
    - **exit_code**: The exit code of the command
    - **stdout**: The standard output of the command
    - **stderr**: The standard error of the command
    
    ## Errors
    - **404**: Job with the specified ID was not found
    - **500**: Server error during job execution
    """
    job_service = JobService(db)
    return job_service.run_job(job_id)

@router.patch("/{job_id}/status", response_model=Job, responses={
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Job not found"
    },
    status.HTTP_500_INTERNAL_SERVER_ERROR: {
        "model": ErrorResponse,
        "description": "Error updating job status"
    }
})
def toggle_job_status(
    job_id: int = Path(..., description="The ID of the job to update"),
    enabled: bool = Body(..., description="True to enable, False to disable the job", embed=True),
    db: Session = Depends(get_db)
):
    """
    Enable or disable a scheduled job.
    
    ## Parameters
    - **job_id**: The unique identifier of the job to update
    - **enabled**: Boolean value to enable (true) or disable (false) the job
    
    ## Returns
    The updated job object with all details
    
    ## Example Request
    ```json
    {
      "enabled": true
    }
    ```
    
    ## Errors
    - **404**: Job with the specified ID was not found
    - **500**: Server error during status update
    """
    job_service = JobService(db)
    return job_service.toggle_job_status(job_id, enabled)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT, responses={
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Job not found"
    },
    status.HTTP_500_INTERNAL_SERVER_ERROR: {
        "model": ErrorResponse,
        "description": "Error deleting job"
    }
})
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
    
    # Check if job exists
    existing_job = job_service.get_job(job_id)
    if not existing_job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID {job_id} not found"
        )
    
    try:
        job_service.delete_job(job_id)
        return None
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error deleting job: {str(e)}"
        )
