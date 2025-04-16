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

import os
import json
import time
import argparse
import logging
import asyncio
from typing import Dict, List, Optional, Any, Union
from datetime import datetime

import requests
from urllib.parse import quote_plus

from gluesync_scheduler.config.settings import settings
from gluesync_scheduler.core.gluesync_sdk_client import gluesync_sdk_client

# Configure logging
logger = logging.getLogger(__name__)


class CoreHubClient:
    """Client for interacting with the Gluesync Core Hub API"""
    
    def __init__(self):
        """Initialize the Core Hub client with configuration from settings"""
        self.base_url = settings.CORE_HUB_URL
        self.entity_start_timeout = settings.ENTITY_START_TIMEOUT  # seconds to wait between entity operations
        self.token = None
        
        # Initialize the SDK client if running as standalone script
        if __name__ == "__main__":
            # Run the async initialization in a synchronous context
            asyncio.run(self._initialize_sdk())
            
        # Try to get token from gluesync SDK client
        self._try_sdk_token()
        
    async def _initialize_sdk(self):
        """Initialize the SDK client if not already initialized"""
        try:
            logger.info("Initializing Gluesync SDK client...")
            await gluesync_sdk_client.initialize()
            logger.info("Gluesync SDK client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Gluesync SDK client: {e}")
            logger.warning("Will attempt to continue without SDK initialization")
    
    def _try_sdk_token(self):
        """Try to get token from Gluesync SDK client if it's initialized"""
        try:
            if gluesync_sdk_client.is_initialized and gluesync_sdk_client.token:
                self.token = gluesync_sdk_client.token
                logger.info("Using token from Gluesync SDK client")
                return True
            else:
                logger.warning("SDK client is not initialized or token is not available")
                logger.info(f"SDK initialized: {gluesync_sdk_client.is_initialized}, Token available: {gluesync_sdk_client.token is not None}")
        except Exception as e:
            logger.warning(f"Could not get token from Gluesync SDK client: {e}")
        return False
            
    def fetch_core_hub(self, path: str, method: str = 'GET', body: Optional[Dict[str, Any]] = None, 
                      params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Make a request to the Core Hub API
        
        Args:
            path: API endpoint path
            method: HTTP method (GET, POST, etc.)
            body: Request body as dictionary
            params: Query parameters as dictionary
            
        Returns:
            Response data as dictionary or None if request failed
        """
        # Check if we have a valid base URL
        if not self.base_url:
            # Try to update from SDK if available
            if gluesync_sdk_client.is_initialized and gluesync_sdk_client.corehub_url:
                self.base_url = gluesync_sdk_client.corehub_url
                settings.update_corehub_url(self.base_url)
                logger.info(f"Updated CoreHub URL from SDK: {self.base_url}")
            else:
                logger.error("CoreHub URL is not set and could not be obtained from SDK")
                return None
                
        # Try to get the SDK token first if we don't have one yet
        if not self.token:
            if not self._try_sdk_token():
                logger.error("Authentication requires SDK token - ensure the gluesync_sdk_client is properly initialized")
                return None
            
        url = f"{self.base_url}{path}"
        headers = {
            'Authorization': f'Bearer {self.token}' if self.token else None,
            'Content-Type': 'application/json'
        }

        # Log request details if in debug mode
        if settings.DEBUG:
            logger.debug(f"Sending request to: {url}")
            logger.debug(f"Method: {method}")
            logger.debug(f"Headers: {headers}")
            logger.debug(f"Body: {body}")
            logger.debug(f"Params: {params}")
        
        try:
            # Make the request
            if method == 'GET':
                response = requests.get(url, headers=headers, params=params)
            elif method == 'POST':
                response = requests.post(url, headers=headers, json=body, params=params)
            elif method == 'PUT':
                response = requests.put(url, headers=headers, json=body, params=params)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, json=body, params=params)
            else:
                logger.error(f"Unsupported HTTP method: {method}")
                return None
            
            # Check if the request was successful
            if response.status_code in [200, 201, 202, 204]:
                # Parse the response JSON if there is any
                if response.text:
                    try:
                        return response.json()
                    except json.JSONDecodeError:
                        logger.warning(f"Response is not valid JSON: {response.text}")
                        return {"text": response.text}
                else:
                    return {}
            else:
                logger.error(f"Request failed with status code {response.status_code}: {response.text}")
                return None
        except Exception as e:
            logger.error(f"Request failed with exception: {str(e)}")
            return None
    
    def get_pipelines(self) -> List[Dict[str, Any]]:
        """Get a list of all pipelines"""
        response = self.fetch_core_hub('/pipelines')
        if response:
            return response.get('pipelines', [])
        return []
    
    def get_pipeline(self, pipeline_id: str) -> Optional[Dict[str, Any]]:
        """Get details of a specific pipeline"""
        response = self.fetch_core_hub(f'/pipelines/{pipeline_id}')
        if response:
            return response
        return None
    
    def get_pipeline_entities(self, pipeline_id: str) -> List[Dict[str, Any]]:
        """Get a list of all entities in a pipeline"""
        response = self.fetch_core_hub(f'/pipelines/{pipeline_id}/entities')
        if response:
            return response.get('entities', [])
        return []
    
    def get_entity(self, pipeline_id: str, entity_id: str) -> Optional[Dict[str, Any]]:
        """Get details of a specific entity in a pipeline"""
        response = self.fetch_core_hub(f'/pipelines/{pipeline_id}/entities/{entity_id}')
        if response:
            return response
        return None
    
    def start_entity(self, pipeline_id: str, entity_id: str, with_snapshot: bool = False) -> bool:
        """Start a specific entity in a pipeline"""
        path = f'/pipelines/{pipeline_id}/entities/{entity_id}/commands/lifecycle/start'
        body = {}
        
        if with_snapshot:
            body['withSnapshot'] = True
            
        response = self.fetch_core_hub(path, method='POST', body=body)
        return response is not None
    
    def stop_entity(self, pipeline_id: str, entity_id: str) -> bool:
        """Stop a specific entity in a pipeline"""
        path = f'/pipelines/{pipeline_id}/entities/{entity_id}/commands/lifecycle/stop'
        response = self.fetch_core_hub(path, method='POST')
        return response is not None
    
    def resync_entity(self, pipeline_id: str, entity_id: str, snapshot_write_method: str = 'UPSERT') -> bool:
        """Trigger a one-time snapshot for a specific entity"""
        path = f'/pipelines/{pipeline_id}/entities/{entity_id}/commands/sync/one-time-snapshot'
        body = {
            'snapshotWriteMethod': snapshot_write_method
        }
        response = self.fetch_core_hub(path, method='POST', body=body)
        return response is not None
    
    def start_pipeline(self, pipeline_id: str, with_snapshot: bool = False) -> bool:
        """Start all entities in a pipeline"""
        path = f'/pipelines/{pipeline_id}/commands/lifecycle/start'
        body = {}
        
        if with_snapshot:
            body['withSnapshot'] = True
            
        response = self.fetch_core_hub(path, method='POST', body=body)
        return response is not None
    
    def stop_pipeline(self, pipeline_id: str) -> bool:
        """Stop all entities in a pipeline"""
        path = f'/pipelines/{pipeline_id}/commands/lifecycle/stop'
        response = self.fetch_core_hub(path, method='POST')
        return response is not None
    
    def resync_pipeline(self, pipeline_id: str, snapshot_write_method: str = 'UPSERT') -> bool:
        """Trigger a one-time snapshot for all entities in a pipeline"""
        path = f'/pipelines/{pipeline_id}/commands/sync/one-time-snapshot'
        body = {
            'snapshotWriteMethod': snapshot_write_method
        }
        response = self.fetch_core_hub(path, method='POST', body=body)
        return response is not None


class PipelineManager:
    """Manager for pipeline operations"""
    
    def __init__(self):
        """Initialize the pipeline manager"""
        self.client = CoreHubClient()
        self.job_identifier = None  # Will be set from the API request
    
    async def update_job_status(self, success: bool, error_message: Optional[str] = None):
        """
        Update the job's execution status in the database.
        
        Args:
            success: Whether the job execution was successful
            error_message: Error message if the job failed (None if successful)
        """
        if not self.job_identifier:
            logger.warning("No job identifier set, cannot update job status")
            return
        
        try:
            from gluesync_scheduler.cli.job_runner import update_job_status
            update_job_status(self.job_identifier, success, error_message)
            logger.info(f"Updated job status for {self.job_identifier}: success={success}")
        except Exception as e:
            logger.error(f"Error updating job status: {str(e)}")
    
    async def execute(self, action: str, pipeline_id: Optional[str] = None, 
               entity_ids: Optional[List[str]] = None, with_snapshot: bool = False,
               snapshot_write_method: str = 'UPSERT') -> bool:
        """Execute a pipeline action
        
        Args:
            action: Action to perform (list, play, pause, resync)
            pipeline_id: ID of the pipeline
            entity_ids: List of entity IDs
            with_snapshot: Whether to include snapshot
            snapshot_write_method: Write method for the snapshot (default: UPSERT)
        """
        try:
            if action == 'list':
                return await self._handle_list_action(pipeline_id)
            elif action == 'resync':
                return await self._handle_resync_action(pipeline_id, entity_ids, snapshot_write_method)
            elif action in ['play', 'pause']:
                return await self._handle_play_pause_action(action, pipeline_id, entity_ids, with_snapshot)
            else:
                logger.error(f"Unknown action: {action}")
                return False
        except Exception as e:
            logger.error(f"Error executing action {action}: {str(e)}")
            return False
    
    async def play_pipeline(self, pipeline_id: str, with_snapshot: bool = False) -> bool:
        """Start all entities in a pipeline
        
        Args:
            pipeline_id: Pipeline ID
            with_snapshot: Start entities with snapshot
            
        Returns:
            bool: True if operation was successful, False otherwise
        """
        logger.info(f"Starting pipeline {pipeline_id} (with_snapshot={with_snapshot})")
        return self.client.start_pipeline(pipeline_id, with_snapshot)
    
    async def play_entities(self, pipeline_id: str, entity_ids: List[str], with_snapshot: bool = False) -> bool:
        """Start specific entities in a pipeline
        
        Args:
            pipeline_id: Pipeline ID
            entity_ids: List of entity IDs to start
            with_snapshot: Start entities with snapshot
            
        Returns:
            bool: True if all operations were successful, False otherwise
        """
        logger.info(f"Starting entities {entity_ids} in pipeline {pipeline_id} (with_snapshot={with_snapshot})")
        success = True
        
        for entity_id in entity_ids:
            logger.info(f"Starting entity {entity_id}")
            result = self.client.start_entity(pipeline_id, entity_id, with_snapshot)
            if not result:
                logger.error(f"Failed to start entity {entity_id}")
                success = False
            
            # Wait a short time between entity operations to avoid overwhelming the Core Hub
            time.sleep(self.client.entity_start_timeout)
        
        return success
    
    async def pause_pipeline(self, pipeline_id: str) -> bool:
        """Stop all entities in a pipeline
        
        Args:
            pipeline_id: Pipeline ID
            
        Returns:
            bool: True if operation was successful, False otherwise
        """
        logger.info(f"Stopping pipeline {pipeline_id}")
        return self.client.stop_pipeline(pipeline_id)
    
    async def pause_entities(self, pipeline_id: str, entity_ids: List[str]) -> bool:
        """Stop specific entities in a pipeline
        
        Args:
            pipeline_id: Pipeline ID
            entity_ids: List of entity IDs to stop
            
        Returns:
            bool: True if all operations were successful, False otherwise
        """
        logger.info(f"Stopping entities {entity_ids} in pipeline {pipeline_id}")
        success = True
        
        for entity_id in entity_ids:
            logger.info(f"Stopping entity {entity_id}")
            result = self.client.stop_entity(pipeline_id, entity_id)
            if not result:
                logger.error(f"Failed to stop entity {entity_id}")
                success = False
            
            # Wait a short time between entity operations to avoid overwhelming the Core Hub
            time.sleep(self.client.entity_start_timeout)
        
        return success
    
    async def resync_pipeline(self, pipeline_id: str, snapshot_write_method: str = 'UPSERT') -> bool:
        """Trigger a one-time snapshot for all entities in a pipeline
        
        Args:
            pipeline_id: Pipeline ID
            snapshot_write_method: Write method for the snapshot (default: UPSERT)
            
        Returns:
            bool: True if operation was successful, False otherwise
        """
        logger.info(f"Resyncing pipeline {pipeline_id} (snapshot_write_method={snapshot_write_method})")
        return self.client.resync_pipeline(pipeline_id, snapshot_write_method)
    
    async def resync_entities(self, pipeline_id: str, entity_ids: List[str], snapshot_write_method: str = 'UPSERT') -> bool:
        """Trigger a one-time snapshot for specific entities in a pipeline
        
        Args:
            pipeline_id: Pipeline ID
            entity_ids: List of entity IDs to resync
            snapshot_write_method: Write method for the snapshot (default: UPSERT)
            
        Returns:
            bool: True if all operations were successful, False otherwise
        """
        logger.info(f"Resyncing entities {entity_ids} in pipeline {pipeline_id} (snapshot_write_method={snapshot_write_method})")
        success = True
        
        for entity_id in entity_ids:
            logger.info(f"Resyncing entity {entity_id}")
            result = self.client.resync_entity(pipeline_id, entity_id, snapshot_write_method)
            if not result:
                logger.error(f"Failed to resync entity {entity_id}")
                success = False
            
            # Wait a short time between entity operations to avoid overwhelming the Core Hub
            time.sleep(self.client.entity_start_timeout)
        
        return success
    
    async def _handle_list_action(self, pipeline_id: Optional[str]) -> bool:
        """Handle the list action
        
        Args:
            pipeline_id: Optional ID of the pipeline
        """
        if pipeline_id:
            # List entities in the pipeline
            entities = self.client.get_pipeline_entities(pipeline_id)
            if entities:
                logger.info(f"Entities in pipeline {pipeline_id}:")
                for entity in entities:
                    logger.info(f"  {entity.get('id')}: {entity.get('name')}")
                return True
            else:
                logger.error(f"Failed to get entities for pipeline {pipeline_id}")
                return False
        else:
            # List all pipelines
            pipelines = self.client.get_pipelines()
            if pipelines:
                logger.info("Available pipelines:")
                for pipeline in pipelines:
                    logger.info(f"  {pipeline.get('id')}: {pipeline.get('name')}")
                return True
            else:
                logger.error("Failed to get pipelines")
                return False
    
    async def _handle_resync_action(self, pipeline_id: Optional[str], entity_ids: Optional[List[str]], 
                             snapshot_write_method: str = 'UPSERT') -> bool:
        """Handle the resync action
        
        Args:
            pipeline_id: ID of the pipeline
            entity_ids: Optional list of entity IDs
            snapshot_write_method: Write method for the snapshot (default: UPSERT)
            
        Returns:
            bool: True if operation was successful, False otherwise
        """
        if not pipeline_id:
            logger.error("Pipeline ID is required for resync action")
            return False
        
        if entity_ids:
            # Resync specific entities
            return await self.resync_entities(pipeline_id, entity_ids, snapshot_write_method)
        else:
            # Resync entire pipeline
            return await self.resync_pipeline(pipeline_id, snapshot_write_method)
    
    async def _handle_play_pause_action(self, action: str, pipeline_id: Optional[str], 
                                 entity_ids: Optional[List[str]], with_snapshot: bool) -> bool:
        """Handle the play or pause action
        
        Args:
            action: 'play' or 'pause'
            pipeline_id: ID of the pipeline
            entity_ids: Optional list of entity IDs
            with_snapshot: Whether to include snapshot
            
        Returns:
            bool: True if operation was successful, False otherwise
        """
        if not pipeline_id:
            logger.error("Pipeline ID is required for play/pause action")
            return False
        
        if action == 'play':
            if entity_ids:
                # Start specific entities
                return await self.play_entities(pipeline_id, entity_ids, with_snapshot)
            else:
                # Start entire pipeline
                return await self.play_pipeline(pipeline_id, with_snapshot)
        elif action == 'pause':
            if entity_ids:
                # Stop specific entities
                return await self.pause_entities(pipeline_id, entity_ids)
            else:
                # Stop entire pipeline
                return await self.pause_pipeline(pipeline_id)
        else:
            logger.error(f"Unknown action: {action}")
            return False


async def main_async():
    """Async main entry point for the script"""
    parser = argparse.ArgumentParser(description="Control Gluesync pipelines and entities")
    parser.add_argument("action", choices=["play", "pause", "resync", "list"], help="Action to perform")
    parser.add_argument("--pipeline", help="Pipeline ID")
    parser.add_argument("--entity", help="Entity ID(s), comma-separated for multiple entities")
    parser.add_argument("--snapshot", action="store_true", help="Create snapshot when starting entities")
    parser.add_argument("--snapshot-write-method", default="UPSERT", help="Write method for snapshot (default: UPSERT)")
    parser.add_argument("--job-id", help="Job identifier for updating job status")
    
    args = parser.parse_args()
    
    # Configure logging
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(os.path.join(log_dir, "play_pause.log"))
        ]
    )
    
    # Parse entity IDs if provided
    entity_ids = None
    if args.entity:
        entity_ids = [e.strip() for e in args.entity.split(",")]
    
    # Initialize pipeline manager
    pipeline_manager = PipelineManager()
    
    # Set job identifier if provided
    if args.job_id:
        pipeline_manager.job_identifier = args.job_id
    
    try:
        # Execute the requested action
        success = await pipeline_manager.execute(
            action=args.action,
            pipeline_id=args.pipeline,
            entity_ids=entity_ids,
            with_snapshot=args.snapshot,
            snapshot_write_method=args.snapshot_write_method
        )
        
        # Update job status if job ID is provided
        if args.job_id:
            await pipeline_manager.update_job_status(success, None if success else "Action failed")
        
        # Exit with appropriate status code
        return 0 if success else 1
    except Exception as e:
        logger.error(f"Error executing action: {str(e)}")
        
        # Update job status if job ID is provided
        if args.job_id:
            await pipeline_manager.update_job_status(False, str(e))
        
        return 1

def main():
    """Main entry point for the script
    
    This function is called when the script is executed directly from the command line.
    It parses command-line arguments and executes the appropriate pipeline action.
    
    The script is used by the scheduler module to execute cron jobs for scheduled tasks.
    
    Example usage:
        python3 play_pause.py play --pipeline pipeline-123 --entity entity-456 --snapshot
        python3 play_pause.py pause --pipeline pipeline-123 --entity entity-456
        python3 play_pause.py resync --pipeline pipeline-123
    """
    # Run the async main function
    return asyncio.run(main_async())

if __name__ == "__main__":
    sys.exit(main())
