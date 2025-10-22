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
from typing import List, Dict, Optional, Any
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import os

from gluesync_scheduler.core.gluesync_sdk_client import gluesync_sdk_client

logger = logging.getLogger(__name__)


class GroupService:
    """Service for managing groups within pipelines via CoreHub API"""
    
    def __init__(self):
        ssl_enabled = os.getenv('SSL_ENABLED', 'False').lower() in ('true', '1', 't')
        ssl_skip_verify = os.getenv('SSL_SKIP_VERIFY', 'False').lower() in ('true', '1', 't')
        self.GLUESYNC_HOST = os.getenv('GLUESYNC_HOST', '')
        self.ssl_verify = not ssl_skip_verify if ssl_enabled else True
        self._setup_session()
    
    def _setup_session(self):
        """Setup HTTP session with retry strategy"""
        self.session = requests.Session()
        
        # Setup retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
    
    async def _get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers using SDK token"""
        try:
            sdk_client = gluesync_sdk_client
            if sdk_client and hasattr(sdk_client, 'token') and sdk_client.token:
                return {
                    'Authorization': f'Bearer {sdk_client.token}',
                    'Content-Type': 'application/json'
                }
            else:
                logger.error("SDK client not available or token not found")
                return {'Content-Type': 'application/json'}
        except Exception as e:
            logger.error(f"Error getting auth headers: {str(e)}")
            return {'Content-Type': 'application/json'}
    
    async def get_pipeline_groups(self, pipeline_id: str) -> List[Dict[str, Any]]:
        """
        Get all groups for a specific pipeline.
        
        Args:
            pipeline_id: The ID of the pipeline
            
        Returns:
            List of group dictionaries with id, name, description, etc.
        """
        try:
            headers = await self._get_auth_headers()
            url = f"{self.GLUESYNC_HOST}/pipelines/{pipeline_id}/config/groups"
            
            logger.info(f"Fetching groups for pipeline {pipeline_id} from {url}")
            
            response = self.session.get(
                url,
                headers=headers,
                verify=self.ssl_verify,
                timeout=30
            )
            
            if response.status_code == 200:
                groups = response.json()
                logger.info(f"Successfully retrieved {len(groups)} groups for pipeline {pipeline_id}")
                return groups
            else:
                logger.error(f"Failed to get groups for pipeline {pipeline_id}: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            logger.error(f"Error getting groups for pipeline {pipeline_id}: {str(e)}")
            return []
    
    async def get_group_entities(self, pipeline_id: str, group_id: str) -> List[str]:
        """
        Get all entity IDs that belong to a specific group.
        
        Args:
            pipeline_id: The ID of the pipeline
            group_id: The ID of the group
            
        Returns:
            List of entity IDs that belong to the group
        """
        try:
            headers = await self._get_auth_headers()
            url = f"{self.GLUESYNC_HOST}/pipelines/{pipeline_id}/config/entities"
            
            logger.info(f"Fetching entities for pipeline {pipeline_id} to filter by group {group_id}")
            
            response = self.session.get(
                url,
                headers=headers,
                verify=self.ssl_verify,
                timeout=30
            )
            
            if response.status_code == 200:
                entities = response.json()
                # Filter entities by group_id
                group_entities = []
                for entity in entities:
                    if isinstance(entity, dict) and entity.get('groupId') == group_id:
                        entity_id = entity.get('id') or entity.get('entityId')
                        if entity_id:
                            group_entities.append(entity_id)
                
                logger.info(f"Found {len(group_entities)} entities in group {group_id} for pipeline {pipeline_id}")
                return group_entities
            else:
                logger.error(f"Failed to get entities for pipeline {pipeline_id}: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            logger.error(f"Error getting entities for group {group_id} in pipeline {pipeline_id}: {str(e)}")
            return []
    
    async def validate_group_exists(self, pipeline_id: str, group_id: str) -> bool:
        """
        Validate that a group exists in the specified pipeline.
        
        Args:
            pipeline_id: The ID of the pipeline
            group_id: The ID of the group to validate
            
        Returns:
            True if the group exists, False otherwise
        """
        try:
            groups = await self.get_pipeline_groups(pipeline_id)
            for group in groups:
                if group.get('id') == group_id:
                    return True
            return False
        except Exception as e:
            logger.error(f"Error validating group {group_id} in pipeline {pipeline_id}: {str(e)}")
            return False
    
    async def get_multiple_groups_entities(self, pipeline_id: str, group_ids: List[str]) -> List[str]:
        """
        Get all entity IDs that belong to multiple groups.
        
        Args:
            pipeline_id: The ID of the pipeline
            group_ids: List of group IDs
            
        Returns:
            List of unique entity IDs that belong to any of the specified groups
        """
        try:
            all_entities = set()
            
            for group_id in group_ids:
                group_entities = await self.get_group_entities(pipeline_id, group_id)
                all_entities.update(group_entities)
            
            entity_list = list(all_entities)
            logger.info(f"Found {len(entity_list)} unique entities across {len(group_ids)} groups in pipeline {pipeline_id}")
            return entity_list
            
        except Exception as e:
            logger.error(f"Error getting entities for groups {group_ids} in pipeline {pipeline_id}: {str(e)}")
            return []


# Global instance
group_service = GroupService()
