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
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status, Query, Path, Body
from sqlalchemy.orm import Session

from gluesync_scheduler.db.database import get_db
from gluesync_scheduler.models.schemas import Setting, SettingsList, SettingUpdate, SettingCreate, ErrorResponse, OperationResponse
from gluesync_scheduler.services.settings_service import SettingsService
from gluesync_scheduler.config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/settings",
    tags=["settings"],
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

@router.get("/", response_model=SettingsList, summary="Get all settings")
async def get_settings(
    skip: int = Query(0, ge=0, description="Number of records to skip for pagination"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of records to return"),
    db: Session = Depends(get_db)
):
    """
    Get a list of all application settings.
    
    ## Parameters
    - **skip**: Number of records to skip (for pagination)
    - **limit**: Maximum number of records to return (for pagination)
    
    ## Returns
    A JSON object containing:
    - **items**: List of setting objects
    - **total**: Total count of settings (without pagination)
    
    ## Example
    ```json
    {
      "items": [
        {
          "id": 1,
          "key": "timezone",
          "value": "UTC",
          "description": "Timezone used for scheduling jobs",
          "created_at": "2025-05-09T10:00:00Z",
          "updated_at": "2025-05-09T10:00:00Z"
        }
      ],
      "total": 1
    }
    ```
    """
    settings_service = SettingsService(db)
    settings_list, total = settings_service.get_all_settings(skip=skip, limit=limit)
    return {"items": settings_list, "total": total}


@router.get("/{key}", response_model=Setting, responses={
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Setting not found"}
}, summary="Get a specific setting")
async def get_setting(key: str = Path(..., description="The key of the setting to retrieve"), db: Session = Depends(get_db)):
    """
    Get a specific setting by key.
    
    ## Parameters
    - **key**: The unique key of the setting to retrieve (e.g., "timezone")
    
    ## Returns
    A complete setting object with all details
    
    ## Example Response
    ```json
    {
      "id": 1,
      "key": "timezone",
      "value": "UTC",
      "description": "Timezone used for scheduling jobs",
      "created_at": "2025-05-09T10:00:00Z",
      "updated_at": "2025-05-09T10:00:00Z"
    }
    ```
    
    ## Errors
    - **404**: Setting with the specified key was not found
    """
    settings_service = SettingsService(db)
    setting = settings_service.get_setting_by_key(key)
    if not setting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Setting with key '{key}' not found"
        )
    return setting


@router.put("/{key}", response_model=Setting, responses={
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Setting not found"},
    status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse, "description": "Invalid setting value"}
}, summary="Update a setting")
async def update_setting(
    key: str = Path(..., description="The key of the setting to update"),
    setting_data: SettingUpdate = Body(..., description="Setting data to update", example={
        "value": "America/New_York",
        "description": "Updated timezone description"
    }),
    db: Session = Depends(get_db)
):
    """
    Update an existing setting.
    
    ## Parameters
    - **key**: The key of the setting to update (e.g., "timezone")
    
    ## Request Body
    - **value**: New value for the setting (required)
    - **description**: Updated description (optional)
    
    ## Returns
    The updated setting object with all details
    
    ## Example Request
    ```json
    {
      "value": "America/New_York",
      "description": "Updated timezone description"
    }
    ```
    
    ## Example Response
    ```json
    {
      "id": 1,
      "key": "timezone",
      "value": "America/New_York",
      "description": "Updated timezone description",
      "created_at": "2025-05-09T10:00:00Z",
      "updated_at": "2025-05-09T14:30:00Z"
    }
    ```
    
    ## Notes
    - When updating the "timezone" setting, the value must be a valid timezone identifier
    - If the TIMEZONE environment variable is set, it will override any value set through the API on restart
    
    ## Errors
    - **400**: Invalid setting value
    - **404**: Setting with the specified key was not found
    """
    settings_service = SettingsService(db)
    
    # Special validation for timezone
    if key == "timezone" and setting_data.value:
        try:
            import pytz
            pytz.timezone(setting_data.value)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid timezone: '{setting_data.value}'"
            )
    
    setting = settings_service.update_setting(key, setting_data)
    
    if not setting:
        # Setting doesn't exist, check if we should create it
        if key in ["timezone"]:  # List of allowed settings to create on-the-fly
            try:
                setting = settings_service.create_setting(
                    SettingCreate(
                        key=key,
                        value=setting_data.value,
                        description=setting_data.description or f"Auto-created {key} setting"
                    )
                )
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Error creating setting: {str(e)}"
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Setting with key '{key}' not found"
            )
    
    return setting


@router.post("/", response_model=Setting, status_code=status.HTTP_201_CREATED, responses={
    status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse, "description": "Invalid request data"},
    status.HTTP_409_CONFLICT: {"model": ErrorResponse, "description": "Setting already exists"}
}, summary="Create a new setting")
async def create_setting(
    setting_data: SettingCreate = Body(..., description="Setting data to create", example={
        "key": "custom_setting",
        "value": "custom_value",
        "description": "A custom application setting"
    }),
    db: Session = Depends(get_db)
):
    """
    Create a new setting.
    
    ## Request Body
    - **key**: Unique key for the setting (required)
    - **value**: Value of the setting (optional)
    - **description**: Description of the setting (optional)
    
    ## Returns
    The created setting object with all details
    
    ## Example Request
    ```json
    {
      "key": "custom_setting",
      "value": "custom_value",
      "description": "A custom application setting"
    }
    ```
    
    ## Example Response
    ```json
    {
      "id": 2,
      "key": "custom_setting",
      "value": "custom_value",
      "description": "A custom application setting",
      "created_at": "2025-05-09T15:00:00Z",
      "updated_at": "2025-05-09T15:00:00Z"
    }
    ```
    
    ## Errors
    - **400**: Invalid request data
    - **409**: Setting with the specified key already exists
    """
    settings_service = SettingsService(db)
    
    # Special validation for timezone
    if setting_data.key == "timezone" and setting_data.value:
        try:
            import pytz
            pytz.timezone(setting_data.value)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid timezone: '{setting_data.value}'"
            )
    
    try:
        setting = settings_service.create_setting(setting_data)
        return setting
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error creating setting: {str(e)}"
        )
