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
            
        url = f"{self.base_url}/api{path}"
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

        response = requests.request(method, url, headers=headers, json=body, params=params)

        # Log response details if in debug mode
        if settings.DEBUG:
            logger.debug(f"Response status code: {response.status_code}")
            logger.debug(f"Response content: {response.text}")

        if response.status_code < 200 or response.status_code >= 300:
            logger.error(f"Request to {url} failed with status code {response.status_code}: {response.text}")
            return None

        try:
            return response.json()
        except json.JSONDecodeError:
            logger.error(f"Failed to parse JSON response: {response.text}")
            return None

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
    
    def resync_pipeline(self, pipeline_id: str, entity_id: Optional[str] = None) -> Dict[str, Any]:
        """Trigger a one-time snapshot sync for a pipeline or specific entity
        
        Args:
            pipeline_id: ID of the pipeline
            entity_id: Optional ID of the entity
            
        Returns:
            Response data from the API
        """
        path = f'/pipelines/{pipeline_id}/commands/sync/one-time-snapshot'
        params = {
            'snapshotWriteMethod': 'UPSERT'
        }
        if entity_id:
            params['entity'] = entity_id
        
        return self.fetch_core_hub(path, method='POST', params=params)

class PipelineManager:
    """Manager for pipeline operations"""
    
    def __init__(self):
        """Initialize the pipeline manager"""
        self.client = CoreHubClient()
    
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
                self._handle_resync_action(pipeline_id, entity_ids)
            elif action in ['play', 'pause']:
                self._handle_play_pause_action(action, pipeline_id, entity_ids, with_snapshot)
            else:
                raise ValueError("Invalid action. Use 'list', 'play', 'pause', or 'resync'.")
        except Exception as e:
            print(f"An error occurred: {str(e)}")
    
    # API-friendly methods for pipeline operations
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
    
    def resync_pipeline(self, pipeline_id: str) -> bool:
        """Resync all entities in a pipeline
        
        Args:
            pipeline_id: Pipeline ID
            
        Returns:
            bool: True if operation was successful, False otherwise
        """
        try:
            self.client.authenticate()
            logger.info(f"Resyncing pipeline {pipeline_id}")
            
            result = self.client.resync_pipeline(pipeline_id)
            if result:
                logger.info(f"Successfully resynced pipeline {pipeline_id}")
                return True
            else:
                logger.error(f"Failed to resync pipeline {pipeline_id}")
                return False
        except Exception as e:
            logger.error(f"Error resyncing pipeline {pipeline_id}: {str(e)}")
            return False
    
    def resync_entities(self, pipeline_id: str, entity_ids: List[str]) -> bool:
        """Resync specific entities in a pipeline
        
        Args:
            pipeline_id: Pipeline ID
            entity_ids: List of entity IDs to resync
            
        Returns:
            bool: True if all operations were successful, False otherwise
        """
        try:
            self.client.authenticate()
            logger.info(f"Resyncing entities {entity_ids} in pipeline {pipeline_id}")
            
            success = True
            for entity_id in entity_ids:
                try:
                    result = self.client.resync_pipeline(pipeline_id, entity_id)
                    if result:
                        logger.info(f"Successfully resynced entity {entity_id} in pipeline {pipeline_id}")
                    else:
                        logger.error(f"Failed to resync entity {entity_id} in pipeline {pipeline_id}")
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
    
    def _handle_resync_action(self, pipeline_id: Optional[str], entity_ids: Optional[List[str]]) -> None:
        """Handle the resync action
        
        Args:
            pipeline_id: ID of the pipeline
            entity_ids: Optional list of entity IDs
        """
        if not pipeline_id:
            print("Pipeline ID is required for resync action")
            return
            
        if entity_ids:
            for entity_id in entity_ids:
                result = self.client.resync_pipeline(pipeline_id, entity_id)
                if result:
                    print(f"Triggered resync for entity: {entity_id}")
                    print(json.dumps(result, indent=2))
        else:
            result = self.client.resync_pipeline(pipeline_id)
            if result:
                print("Triggered pipeline-wide resync")
                print(json.dumps(result, indent=2))
    
    def _handle_play_pause_action(self, action: str, pipeline_id: Optional[str], 
                                 entity_ids: Optional[List[str]], with_snapshot: bool) -> None:
        """Handle the play or pause action
        
        Args:
            action: 'play' or 'pause'
            pipeline_id: ID of the pipeline
            entity_ids: Optional list of entity IDs
            with_snapshot: Whether to include snapshot
        """
        if not pipeline_id:
            raise ValueError("Pipeline ID is required for play/pause actions")

        entities = self.client.get_entities(pipeline_id)

        if not entities:
            print(f"No entities found for pipeline {pipeline_id}")
            return

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

            # Wait between entity operations, except for the last one
            if i < len(entities) - 1:
                print(f"Waiting for {self.client.entity_start_timeout} seconds before the next action...")
                time.sleep(self.client.entity_start_timeout)

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
    
    # Parse arguments
    args = parser.parse_args()
    
    try:
        # Log environment information for debugging
        logger.info("Starting play_pause.py script")
        logger.info(f"Running with CoreHub URL: {settings.CORE_HUB_URL}")
        logger.info(f"License file path: {settings.GLUESYNC_LICENSE_FILE}")
        logger.info(f"Security config path: {settings.GLUESYNC_SECURITY_CONFIG}")
        logger.info(f"SDK module tag: {settings.GLUESYNC_MODULE_TAG}")
        logger.info(f"Using SSL: {settings.GLUESYNC_USE_SSL}")
        
        # Make sure logs directory exists
        logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
        os.makedirs(logs_dir, exist_ok=True)
        
        # Execute the requested action
        manager = PipelineManager()
        manager.execute(args.action, args.pipeline, args.entity, args.snapshot)
        
    except Exception as e:
        logger.error(f"An error occurred in main execution: {e}", exc_info=True)

if __name__ == "__main__":
    main()
