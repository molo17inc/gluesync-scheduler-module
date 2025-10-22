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
import pytz
import os
from typing import List, Optional, Dict, Tuple
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from gluesync_scheduler.models.models import Setting
from gluesync_scheduler.models.schemas import SettingCreate, SettingUpdate

logger = logging.getLogger(__name__)

class SettingsService:
    """Service for managing application settings"""
    
    def __init__(self, db: Session):
        """Initialize the settings service with a database session"""
        self.db = db
    
    def get_all_settings(self, skip: int = 0, limit: int = 100) -> Tuple[List[Setting], int]:
        """
        Get all settings with pagination
        
        Args:
            skip: Number of records to skip
            limit: Maximum number of records to return
            
        Returns:
            A tuple containing (list of settings, total count)
        """
        # Get total count
        total = self.db.query(Setting).count()
        
        # Get paginated results
        settings_list = self.db.query(Setting).order_by(Setting.key).offset(skip).limit(limit).all()
        
        return settings_list, total
    
    def get_setting_by_key(self, key: str) -> Optional[Setting]:
        """
        Get a single setting by its key
        
        Args:
            key: The setting key to look up
            
        Returns:
            The setting object or None if not found
        """
        return self.db.query(Setting).filter(Setting.key == key).first()
    
    def create_setting(self, setting_data: SettingCreate) -> Setting:
        """
        Create a new setting
        
        Args:
            setting_data: The setting data
            
        Returns:
            The created setting object
            
        Raises:
            ValueError: If a setting with the same key already exists
        """
        # Check if setting with this key already exists
        existing = self.get_setting_by_key(setting_data.key)
        if existing:
            raise ValueError(f"Setting with key '{setting_data.key}' already exists")
        
        # Create new setting
        new_setting = Setting(
            key=setting_data.key,
            value=setting_data.value,
            description=setting_data.description
        )
        
        try:
            self.db.add(new_setting)
            self.db.commit()
            self.db.refresh(new_setting)
            
            # If we just created a timezone setting, update the global settings
            if setting_data.key == "timezone" and setting_data.value:
                self._update_global_timezone(setting_data.value)
            
            return new_setting
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Error creating setting: {str(e)}")
            raise
    
    def update_setting(self, key: str, setting_data: SettingUpdate) -> Optional[Setting]:
        """
        Update an existing setting
        
        Args:
            key: The key of the setting to update
            setting_data: The new setting data
            
        Returns:
            The updated setting object or None if not found
        """
        # Get the existing setting
        setting = self.get_setting_by_key(key)
        if not setting:
            return None
        
        # Update fields
        if setting_data.value is not None:
            setting.value = setting_data.value
        
        if setting_data.description is not None:
            setting.description = setting_data.description
        
        try:
            self.db.commit()
            self.db.refresh(setting)
            
            # If we just updated the timezone setting, update the global settings
            if key == "timezone" and setting_data.value:
                self._update_global_timezone(setting_data.value)
            
            return setting
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Error updating setting: {str(e)}")
            raise
    
    def delete_setting(self, key: str) -> bool:
        """
        Delete a setting
        
        Args:
            key: The key of the setting to delete
            
        Returns:
            True if deleted, False if not found
        """
        setting = self.get_setting_by_key(key)
        if not setting:
            return False
        
        try:
            self.db.delete(setting)
            self.db.commit()
            return True
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.error(f"Error deleting setting: {str(e)}")
            raise
    
    def _update_global_timezone(self, timezone_value: str) -> bool:
        """
        Update the global timezone setting if the timezone is valid
        
        Args:
            timezone_value: The timezone string (e.g., 'America/New_York')
            
        Returns:
            True if updated, False if timezone is invalid
        """
        try:
            # Validate timezone
            pytz.timezone(timezone_value)
            
            # Note: Global timezone setting is no longer used since we load from DB on startup
            logger.info(f"Validated timezone setting: {timezone_value}")
            return True
        except pytz.exceptions.UnknownTimeZoneError:
            logger.error(f"Invalid timezone: {timezone_value}")
            return False
    
    def get_timezone(self) -> str:
        """
        Get the current timezone setting
        
        Returns:
            The current timezone string
        """
        # First try to get from database
        timezone_setting = self.get_setting_by_key("timezone")
        if timezone_setting and timezone_setting.value:
            return timezone_setting.value
        
        # Fall back to global settings (from environment)
        return os.getenv('TIMEZONE', 'UTC')
    
    def initialize_default_settings(self) -> Dict[str, str]:
        """
        Initialize default settings if they don't exist
        
        Returns:
            Dictionary of created settings
        """
        created = {}
        
        # Define default settings
        defaults = {
            "timezone": {
                "value": os.getenv('TIMEZONE', 'UTC'),
                "description": "Timezone used for scheduling jobs"
            }
        }
        
        # Create any missing settings
        for key, config in defaults.items():
            existing = self.get_setting_by_key(key)
            if not existing:
                setting_data = SettingCreate(
                    key=key,
                    value=config["value"],
                    description=config["description"]
                )
                created_setting = self.create_setting(setting_data)
                created[key] = created_setting.value
                logger.info(f"Created default setting: {key} = {config['value']}")
            else:
                logger.info(f"Default setting already exists: {key} = {existing.value}")
        
        return created
