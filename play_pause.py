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

from config import settings
from gluesync_sdk_client import gluesync_sdk_client

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
        """Try to get token from gluesync SDK client if it's initialized"""
        try:
            if gluesync_sdk_client.is_initialized and gluesync_sdk_client.token:
                self.token = gluesync_sdk_client.token
                logger.info("Using token from gluesync SDK client")
                return True
            else:
                logger.warning("SDK client is not initialized or token is not available")
                logger.info(f"SDK initialized: {gluesync_sdk_client.is_initialized}, Token available: {gluesync_sdk_client.token is not None}")
        except Exception as e:
            logger.warning(f"Could not get token from gluesync SDK client: {e}")
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

        # For cron jobs and standalone scripts, we need to be more aggressive with SSL verification skipping
        # If we're in a TLS environment (using HTTPS) and SSL_SKIP_VERIFY is True, skip verification
        should_verify = not settings.SSL_SKIP_VERIFY
        
        # If URL starts with https, additionally check if we should force disable verification
        force_no_verify = url.startswith('https://') and os.environ.get('FORCE_NO_SSL_VERIFY', 'False').lower() in ('true', '1', 'yes')
        
        # Final verification setting
        verify = not (should_verify is False or force_no_verify)
        
        logger.info(f"Making request to {url} with SSL verification {'disabled' if not verify else 'enabled'}")
        logger.info(f"SSL settings: SSL_SKIP_VERIFY={settings.SSL_SKIP_VERIFY}, FORCE_NO_SSL_VERIFY={force_no_verify}")
        
        try:
            response = requests.request(method, url, headers=headers, json=body, params=params, verify=verify)
        except requests.exceptions.SSLError as e:
            logger.error(f"SSL Error connecting to {url}: {str(e)}")
            logger.error("Attempting to retry request with SSL verification disabled as a fallback")
            
            # As a last resort, try one more time with verification disabled
            try:
                response = requests.request(method, url, headers=headers, json=body, params=params, verify=False)
                logger.warning("Successfully connected with SSL verification disabled")
            except Exception as retry_e:
                logger.error(f"Still failed after disabling SSL verification: {str(retry_e)}")
                raise e  # Raise the original error
        except Exception as e:
            logger.error(f"Error connecting to {url}: {str(e)}")
            raise

        # Log response details if in debug mode
        if settings.DEBUG:
            logger.debug(f"Response status code: {response.status_code}")
            logger.debug(f"Response content: {response.text}")

        # Check if the response status code is in the 2XX range
        if response.status_code < 200 or response.status_code >= 300:
            logger.error(f"Request to {url} failed with status code {response.status_code}: {response.text}")
            return None

        # Success if status code is in 2XX range
        try:
            # Try to parse JSON but don't fail if not JSON
            return response.json()
        except json.JSONDecodeError:
            logger.info(f"No JSON response but status code {response.status_code} indicates success")
            return {'success': True, 'status_code': response.status_code}

    def authenticate(self) -> None:
        """Authenticate with the Core Hub API and store the token"""
        # Use the SDK token only
        if self._try_sdk_token() and self.token:
            logger.info("Using authentication token from gluesync SDK client")
            return

        # If SDK token is not available, raise an exception
        raise Exception('Authentication requires SDK token - ensure the gluesync_sdk_client is properly initialized')
    
    def get_pipelines(self) -> List[Dict[str, Any]]:
        """Get a list of all pipelines
        
        Returns:
            List of pipeline objects
        """
        return self.fetch_core_hub('/pipelines')
    
    @staticmethod
    def safe_encode(s: str) -> str:
        """Safely encode a string for use in URLs"""
        return quote_plus(s).replace(".", "%2E")
    
    def get_pipeline_config(self, pipeline_id: str) -> Dict[str, Any]:
        """Get the configuration for a specific pipeline
        
        Args:
            pipeline_id: ID of the pipeline
            
        Returns:
            Pipeline configuration
        """
        return self.fetch_core_hub(f"/pipelines/{pipeline_id}/config")
    
    def get_entities(self, pipeline_id: str) -> List[Dict[str, str]]:
        """Get a list of entities for a specific pipeline
        
        Args:
            pipeline_id: ID of the pipeline
            
        Returns:
            List of entity objects with entityId and entityName
        """
        pipeline_config = self.fetch_core_hub(
            f"/pipelines/{pipeline_id}/config",
            method='GET'
        )
        
        entities = []
        for entity in pipeline_config.get('entities', []):
            entities.append({
                'entityId': entity['entityId'],
                'entityName': entity['entityName']
            })

        print(f"Total entities found: {len(entities)}")
        
        for entity in entities:
            print(f"  - Entity: {entity['entityName']} (ID: {entity['entityId']})")

        return entities
    
    def play_entity(self, pipeline_id: str, entity_id: str, with_snapshot: bool = False) -> None:
        """Start synchronization for a specific entity
        
        Args:
            pipeline_id: ID of the pipeline
            entity_id: ID of the entity
            with_snapshot: Whether to include snapshot
        """
        params = {
            'entity': entity_id,
            'withSnapshot': 'true' if with_snapshot else 'false'
        }
        self.fetch_core_hub(
            f"/pipelines/{pipeline_id}/commands/sync/start",
            method='POST',
            params=params
        )
        print(f"Started sync for entity: {entity_id}{' with snapshot' if with_snapshot else ''}")
    
    def pause_entity(self, pipeline_id: str, entity_id: str) -> None:
        """Stop synchronization for a specific entity
        
        Args:
            pipeline_id: ID of the pipeline
            entity_id: ID of the entity
        """
        params = {'entity': entity_id}
        self.fetch_core_hub(
            f"/pipelines/{pipeline_id}/commands/sync/stop",
            method='POST',
            params=params
        )
        print(f"Paused sync for entity: {entity_id}")
        
    def resync_entity(self, pipeline_id: str, entity_id: str) -> None:
        """Resynchronize a specific entity
        
        Args:
            pipeline_id: ID of the pipeline
            entity_id: ID of the entity
        """
        params = {'entity': entity_id}
        self.fetch_core_hub(
            f"/pipelines/{pipeline_id}/commands/sync/resync",
            method='POST',
            params=params
        )
        print(f"Resynced entity: {entity_id}")
    
    def resync_pipeline(self, pipeline_id: str, entity_id: Optional[str] = None, snapshot_write_method: str = 'UPSERT') -> Dict[str, Any]:
        """Trigger a one-time snapshot sync for a pipeline or specific entity
        
        Args:
            pipeline_id: ID of the pipeline
            entity_id: Optional ID of the entity
            snapshot_write_method: Write method for the snapshot (default: UPSERT)
            
        Returns:
            Response data from the API
        """
        path = f'/pipelines/{pipeline_id}/commands/sync/one-time-snapshot'
        params = {
            'snapshotWriteMethod': snapshot_write_method
        }
        if entity_id:
            params['entity'] = entity_id
        
        return self.fetch_core_hub(path, method='POST', params=params)

class PipelineManager:
    """Manager for pipeline operations"""
    
    def __init__(self):
        """Initialize the pipeline manager"""
        self.client = CoreHubClient()
        self.job_id = None  # Will be set from the command line arguments
        
    def update_job_status(self, success: bool, error_message: Optional[str] = None) -> None:
        """
        Update the job's execution status in the database.
        
        Args:
            success: Whether the job execution was successful
            error_message: Error message if the job failed (None if successful)
        """
        if not self.job_id:
            logger.warning("No job_id provided, cannot update job status")
            return
            
        from database import get_db
        from models import ScheduledJob
        
        db = get_db()
        job = db.query(ScheduledJob).filter(ScheduledJob.id == self.job_id).first()
        if not job:
            logger.error(f"Job with ID {self.job_id} not found when updating status")
            return
            
        current_time = datetime.now()
        job.last_run = current_time
        
        if success:
            job.last_successful_run = current_time
            job.last_error_message = None
            job.last_run_error_time = None
        else:
            job.last_error_message = error_message
            job.last_run_error_time = current_time
            
        db.commit()
        logger.info(f"Updated job {self.job_id} status - success: {success}, error: {error_message}")
    
    def execute(self, action: str, pipeline_id: Optional[str] = None, 
               entity_ids: Optional[List[str]] = None, with_snapshot: bool = False) -> None:
        """Execute a pipeline action
        
        Args:
            action: Action to perform (list, play, pause, resync)
            pipeline_id: ID of the pipeline
            entity_ids: List of entity IDs
            with_snapshot: Whether to include snapshot
        """
        try:
            # Authenticate with Core Hub
            self.client.authenticate()
            
            if action == 'list':
                self._handle_list_action(pipeline_id)
            elif action == 'resync':
                success = self._handle_resync_action(pipeline_id, entity_ids)
                self.update_job_status(success, None if success else "Resync operation failed")
            elif action in ['play', 'pause']:
                success = self._handle_play_pause_action(action, pipeline_id, entity_ids, with_snapshot)
                action_str = "Play" if action == 'play' else "Pause"
                self.update_job_status(success, None if success else f"{action_str} operation failed")
            else:
                raise ValueError("Invalid action. Use 'list', 'play', 'pause', or 'resync'.")
        except Exception as e:
            logger.error(f"An error occurred: {str(e)}")
            self.update_job_status(False, str(e))
            raise
    def play_pipeline(self, pipeline_id: str, with_snapshot: bool = False) -> bool:
        """Start all entities in a pipeline
        
        Args:
            pipeline_id: Pipeline ID
            with_snapshot: Start entities with snapshot
            
        Returns:
            bool: True if operation was successful, False otherwise
        """
        try:
            self.client.authenticate()
            logger.info(f"Starting pipeline {pipeline_id} with snapshot={with_snapshot}")
            entities = self.client.get_entities(pipeline_id)
            
            if not entities:
                logger.warning(f"No entities found for pipeline {pipeline_id}")
                return False
                
            for i, entity in enumerate(entities):
                try:
                    self.client.play_entity(pipeline_id, entity['entityId'], with_snapshot)
                    logger.info(f"Started entity {entity['entityId']} in pipeline {pipeline_id}")
                    
                    # Wait between entity operations, except for the last one
                    if i < len(entities) - 1:
                        logger.debug(f"Waiting for {self.client.entity_start_timeout} seconds before the next action...")
                        time.sleep(self.client.entity_start_timeout)
                except Exception as e:
                    logger.error(f"Error starting entity {entity['entityId']}: {str(e)}")
                    return False
            return True
        except Exception as e:
            logger.error(f"Error starting pipeline {pipeline_id}: {str(e)}")
            return False
    
    def play_entities(self, pipeline_id: str, entity_ids: List[str], with_snapshot: bool = False) -> bool:
        """Start specific entities in a pipeline
        
        Args:
            pipeline_id: Pipeline ID
            entity_ids: List of entity IDs to start
            with_snapshot: Start entities with snapshot
            
        Returns:
            bool: True if all operations were successful, False otherwise
        """
        try:
            self.client.authenticate()
            logger.info(f"Starting entities {entity_ids} in pipeline {pipeline_id} with snapshot={with_snapshot}")
            
            entities = self.client.get_entities(pipeline_id)
            if not entities:
                logger.warning(f"No entities found for pipeline {pipeline_id}")
                return False
                
            target_entities = [entity for entity in entities if entity['entityId'] in entity_ids]
            if not target_entities:
                logger.warning(f"Entities {', '.join(entity_ids)} not found in pipeline {pipeline_id}")
                return False
                
            for i, entity in enumerate(target_entities):
                try:
                    self.client.play_entity(pipeline_id, entity['entityId'], with_snapshot)
                    logger.info(f"Started entity {entity['entityId']} in pipeline {pipeline_id}")
                    
                    # Wait between entity operations, except for the last one
                    if i < len(target_entities) - 1:
                        logger.debug(f"Waiting for {self.client.entity_start_timeout} seconds before the next action...")
                        time.sleep(self.client.entity_start_timeout)
                except Exception as e:
                    logger.error(f"Error starting entity {entity['entityId']}: {str(e)}")
                    return False
            return True
        except Exception as e:
            logger.error(f"Error starting entities in pipeline {pipeline_id}: {str(e)}")
            return False
    
    def pause_pipeline(self, pipeline_id: str) -> bool:
        """Stop all entities in a pipeline
        
        Args:
            pipeline_id: Pipeline ID
            
        Returns:
            bool: True if operation was successful, False otherwise
        """
        try:
            self.client.authenticate()
            logger.info(f"Stopping pipeline {pipeline_id}")
            
            entities = self.client.get_entities(pipeline_id)
            if not entities:
                logger.warning(f"No entities found for pipeline {pipeline_id}")
                return False
                
            for entity in entities:
                try:
                    self.client.pause_entity(pipeline_id, entity['entityId'])
                    logger.info(f"Stopped entity {entity['entityId']} in pipeline {pipeline_id}")
                except Exception as e:
                    logger.error(f"Error stopping entity {entity['entityId']}: {str(e)}")
                    return False
            return True
        except Exception as e:
            logger.error(f"Error stopping pipeline {pipeline_id}: {str(e)}")
            return False
    
    def pause_entities(self, pipeline_id: str, entity_ids: List[str]) -> bool:
        """Stop specific entities in a pipeline
        
        Args:
            pipeline_id: Pipeline ID
            entity_ids: List of entity IDs to stop
            
        Returns:
            bool: True if all operations were successful, False otherwise
        """
        try:
            self.client.authenticate()
            logger.info(f"Stopping entities {entity_ids} in pipeline {pipeline_id}")
            
            entities = self.client.get_entities(pipeline_id)
            if not entities:
                logger.warning(f"No entities found for pipeline {pipeline_id}")
                return False
                
            target_entities = [entity for entity in entities if entity['entityId'] in entity_ids]
            if not target_entities:
                logger.warning(f"Entities {', '.join(entity_ids)} not found in pipeline {pipeline_id}")
                return False
                
            for entity in target_entities:
                try:
                    self.client.pause_entity(pipeline_id, entity['entityId'])
                    logger.info(f"Stopped entity {entity['entityId']} in pipeline {pipeline_id}")
                except Exception as e:
                    logger.error(f"Error stopping entity {entity['entityId']}: {str(e)}")
                    return False
            return True
        except Exception as e:
            logger.error(f"Error stopping entities in pipeline {pipeline_id}: {str(e)}")
            return False
    
    def resync_pipeline(self, pipeline_id: str, snapshot_write_method: str = 'UPSERT') -> bool:
        """Trigger a one-time snapshot for all entities in a pipeline
        
        Args:
            pipeline_id: Pipeline ID
            snapshot_write_method: Write method for the snapshot (default: UPSERT)
            
        Returns:
            bool: True if operation was successful, False otherwise
        """
        try:
            self.client.authenticate()
            logger.info(f"Triggering one-time snapshot for pipeline {pipeline_id} with method {snapshot_write_method}")
            
            # Pass the snapshot_write_method to the client
            result = self.client.resync_pipeline(pipeline_id, snapshot_write_method=snapshot_write_method)
            if result:
                logger.info(f"Successfully triggered one-time snapshot for pipeline {pipeline_id}")
                return True
            else:
                logger.error(f"Failed to trigger one-time snapshot for pipeline {pipeline_id}")
                return False
        except Exception as e:
            logger.error(f"Error triggering one-time snapshot for pipeline {pipeline_id}: {str(e)}")
            return False
    
    def resync_entities(self, pipeline_id: str, entity_ids: List[str], snapshot_write_method: str = 'UPSERT') -> bool:
        """Trigger a one-time snapshot for specific entities in a pipeline
        
        Args:
            pipeline_id: Pipeline ID
            entity_ids: List of entity IDs to resync
            snapshot_write_method: Write method for the snapshot (default: UPSERT)
            
        Returns:
            bool: True if all operations were successful, False otherwise
        """
        try:
            self.client.authenticate()
            logger.info(f"Triggering one-time snapshot for entities {entity_ids} in pipeline {pipeline_id} with method {snapshot_write_method}")
            
            success = True
            for entity_id in entity_ids:
                try:
                    result = self.client.resync_pipeline(pipeline_id, entity_id, snapshot_write_method=snapshot_write_method)
                    if result:
                        logger.info(f"Successfully triggered one-time snapshot for entity {entity_id} in pipeline {pipeline_id}")
                    else:
                        logger.error(f"Failed to trigger one-time snapshot for entity {entity_id} in pipeline {pipeline_id}")
                        success = False
                except requests.exceptions.SSLError as e:
                    logger.error(f"SSL Error resyncing entity {entity_id}: {str(e)}")
                    logger.warning(f"SSL verification is {'disabled' if settings.SSL_SKIP_VERIFY else 'enabled'}. Set SSL_SKIP_VERIFY=True if using self-signed certificates.")
                    success = False
                except Exception as e:
                    logger.error(f"Error resyncing entity {entity_id}: {str(e)}")
                    success = False
            return success
        except Exception as e:
            logger.error(f"Error resyncing entities in pipeline {pipeline_id}: {str(e)}")
            return False
    
    def _handle_list_action(self, pipeline_id: Optional[str]) -> None:
        """Handle the list action
        
        Args:
            pipeline_id: Optional ID of the pipeline
        """
        if pipeline_id:
            entities = self.client.get_entities(pipeline_id)
            print(json.dumps(entities, indent=2))
        else:
            pipelines = self.client.get_pipelines()
            print(json.dumps(pipelines, indent=2))
    
    def _handle_resync_action(self, pipeline_id: Optional[str], entity_ids: Optional[List[str]]) -> bool:
        """Handle the resync action
        
        Args:
            pipeline_id: ID of the pipeline
            entity_ids: Optional list of entity IDs
            
        Returns:
            bool: True if operation was successful, False otherwise
        """
        if not pipeline_id:
            print("Pipeline ID is required for resync action")
            return False
            
        if entity_ids:
            for entity_id in entity_ids:
                result = self.client.resync_pipeline(pipeline_id, entity_id)
                if result:
                    print(f"Triggered resync for entity: {entity_id}")
                    print(json.dumps(result, indent=2))
                else:
                    return False
        else:
            result = self.client.resync_pipeline(pipeline_id)
            if result:
                print("Triggered pipeline-wide resync")
                print(json.dumps(result, indent=2))
            else:
                return False
        return True
    
    def _handle_play_pause_action(self, action: str, pipeline_id: Optional[str], 
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
            raise ValueError("Pipeline ID is required for play/pause actions")

        entities = self.client.get_entities(pipeline_id)

        if not entities:
            print(f"No entities found for pipeline {pipeline_id}")
            return False

        if entity_ids:
            entities = [entity for entity in entities if entity['entityId'] in entity_ids]
            if not entities:
                raise ValueError(f"Entities {', '.join(entity_ids)} not found in pipeline {pipeline_id}")

        for i, entity in enumerate(entities):
            try:
                if action == 'play':
                    self.client.play_entity(pipeline_id, entity['entityId'], with_snapshot)
                else:
                    self.client.pause_entity(pipeline_id, entity['entityId'])
            except Exception as e:
                print(f"Error {'playing' if action == 'play' else 'pausing'} entity {entity['entityId']}: {str(e)}")
                return False

            # Wait between entity operations, except for the last one
            if i < len(entities) - 1:
                print(f"Waiting for {self.client.entity_start_timeout} seconds before the next action...")
                time.sleep(self.client.entity_start_timeout)
        return True

def main():
    """Main entry point for the script
    
    This function is called when the script is executed directly from the command line.
    It parses command-line arguments and executes the appropriate pipeline action.
    
    The script is used by the scheduler module to execute cron jobs for scheduled tasks.
    
    Example usage:
        python play_pause.py play --pipeline pipeline-123 --entity entity-456 --snapshot
        python play_pause.py pause --pipeline pipeline-123 --entity entity-456
        python play_pause.py resync --pipeline pipeline-123
    """
    # Ensure SSL settings are properly loaded when running as standalone script
    # For cron jobs, we should default to skipping SSL verification
    if 'SSL_SKIP_VERIFY' not in os.environ or os.environ.get('SSL_SKIP_VERIFY').lower() not in ('true', '1', 'yes'):
        os.environ['SSL_SKIP_VERIFY'] = 'True'
        # Force a reload of settings to ensure they reflect latest environment variables
        settings.reload_from_env()
        
    # Force disable SSL verification for cron jobs in HTTPS environments
    os.environ['FORCE_NO_SSL_VERIFY'] = 'True'
    
    # Configure more verbose logging for standalone execution
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "play_pause.log"))
        ]
    )
    
    # Create argument parser
    parser = argparse.ArgumentParser(
        description="Gluesync Pipeline Management Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    # Define arguments
    parser.add_argument(
        'action', 
        choices=['list', 'play', 'pause', 'resync'], 
        help="Action to perform (list, play, pause, resync)"
    )
    parser.add_argument(
        '--pipeline', 
        help="Pipeline ID for actions"
    )
    parser.add_argument(
        '--entity', 
        nargs='+', 
        help="One or more entity IDs for play/pause actions"
    )
    parser.add_argument(
        '--snapshot', 
        action='store_true', 
        help="Start entities with snapshot"
    )
    parser.add_argument(
        '--job-id', 
        help="Job ID for tracking execution status"
    )
    
    # Parse arguments
    args = parser.parse_args()
    
    try:
        # Log environment information for debugging
        logger.info("Starting play_pause.py script")
        logger.info(f"Running with CoreHub URL: {settings.CORE_HUB_URL}")
        logger.info(f"License file path: {settings.GLUESYNC_LICENSE_FILE}")
        logger.info(f"Security config path: {settings.GLUESYNC_SECURITY_CONFIG}")
        logger.info(f"SDK module tag: {settings.GLUESYNC_MODULE_TAG}")
        logger.info(f"Using SSL: {settings.SSL_ENABLED}")
        logger.info(f"SSL_SKIP_VERIFY: {settings.SSL_SKIP_VERIFY}")
        logger.info(f"FORCE_NO_SSL_VERIFY: {os.environ.get('FORCE_NO_SSL_VERIFY', 'Not set')}")
        
        # Log whether SSL verification will be performed
        verify_status = not (settings.SSL_SKIP_VERIFY or os.environ.get('FORCE_NO_SSL_VERIFY', 'False').lower() in ('true', '1', 'yes'))
        logger.info(f"SSL Certificate verification will be: {'ENABLED' if verify_status else 'DISABLED'}")
        
        # Make sure logs directory exists
        logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
        os.makedirs(logs_dir, exist_ok=True)
        
        # Set the job ID for tracking execution status
        manager = PipelineManager()
        manager.job_id = args.job_id
        
        # Execute the requested action
        manager.execute(args.action, args.pipeline, args.entity, args.snapshot)
        
    except Exception as e:
        logger.error(f"An error occurred in main execution: {e}", exc_info=True)

if __name__ == "__main__":
    main()
