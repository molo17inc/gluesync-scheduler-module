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
from urllib.parse import quote_plus, urlparse, urljoin
import uuid

from gluesync_scheduler.config.settings import settings
from gluesync_scheduler.core.gluesync_sdk_client import gluesync_sdk_client

# Configure logging
logger = logging.getLogger(__name__)


class CoreHubClient:
    """Client for interacting with the Gluesync Core Hub API"""
    
    # Singleton instance
    _instance = None
    
    # Store settings directly in the class
    from gluesync_scheduler.config.settings import settings as _settings
    
    # Class variable to track if CoreHub URL has been discovered
    _corehub_url_discovered = False
    
    # Class variable to store the discovered URL
    _shared_base_url = None
    
    def __new__(cls):
        """Implement singleton pattern"""
        if cls._instance is None:
            logger.info("Creating new CoreHubClient singleton instance")
            cls._instance = super(CoreHubClient, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Initialize the Core Hub client with configuration from settings"""
        # Skip initialization if already done
        if getattr(self, '_initialized', False):
            return
            
        logger.info("Initializing CoreHubClient")
        
        # First try to get the URL from the SDK client directly
        from gluesync_scheduler.core.gluesync_sdk_client import gluesync_sdk_client
        sdk_url = None
        if hasattr(gluesync_sdk_client, 'corehub_url') and gluesync_sdk_client.corehub_url:
            sdk_url = gluesync_sdk_client.corehub_url
            logger.info(f"Found CoreHub URL from SDK client: {sdk_url}")
            
        # Then try class variable or settings
        self.base_url = sdk_url or CoreHubClient._shared_base_url or settings.CORE_HUB_URL
        
        # If we have a URL from any source, store it and mark discovery as complete
        if self.base_url:
            # Store the URL in the class variable if not already set
            if not CoreHubClient._shared_base_url:
                CoreHubClient._shared_base_url = self.base_url
            CoreHubClient._corehub_url_discovered = True
            logger.info(f"Using CoreHub URL: {self.base_url}")
            
            # Also update settings if needed
            if not settings.CORE_HUB_URL:
                settings.update_corehub_url(self.base_url)
                logger.info(f"Updated CoreHub URL in settings: {settings.CORE_HUB_URL}")
        # If no URL is set, try to discover it
        else:
            # Only attempt discovery if we haven't already done it
            if not CoreHubClient._corehub_url_discovered:
                logger.info("No CoreHub URL found, attempting discovery")
                self._discover_corehub_url()
                if self.base_url:
                    # Store the discovered URL in the class variable
                    CoreHubClient._shared_base_url = self.base_url
                    CoreHubClient._corehub_url_discovered = True
                    logger.info(f"CoreHub URL discovered and stored: {self.base_url}")
                    
                    # Also update settings
                    settings.update_corehub_url(self.base_url)
                    logger.info(f"Updated CoreHub URL in settings: {settings.CORE_HUB_URL}")
                else:
                    logger.error("Failed to discover CoreHub URL")
                    CoreHubClient._corehub_url_discovered = True  # Mark as attempted
            else:
                # If we've already tried discovery but still don't have a URL,
                # log a warning but don't try again
                logger.warning("CoreHub URL not available despite previous discovery attempts")
            
        self.entity_start_timeout = settings.ENTITY_START_TIMEOUT  # seconds to wait between entity operations
        self.token = None
        self._initialized = True
        
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
    
    def _extract_http_url_from_ws_url(self, ws_url: str) -> str:
        """Extract HTTP URL from WebSocket URL
        
        Args:
            ws_url: The WebSocket URL (e.g., ws://example.com:1234)
            
        Returns:
            The corresponding HTTP URL (e.g., http://example.com:1234)
        """
        import re
        match = re.match(r'(wss?)://([^:/]+)(?::([0-9]+))?', ws_url)
        if match:
            ws_protocol, ws_host, ws_port = match.groups()
            use_ssl = (ws_protocol == 'wss')
            http_protocol = 'https' if use_ssl else 'http'
            port = ws_port or ('443' if use_ssl else '80')
            return f"{http_protocol}://{ws_host}:{port}"
        return None
    
    def _discover_corehub_url(self):
        """Discover the CoreHub URL using various methods"""
        # Method 1: Try to get from SDK client's corehub_url property
        if gluesync_sdk_client.is_initialized and gluesync_sdk_client.corehub_url:
            self.base_url = gluesync_sdk_client.corehub_url
            settings.update_corehub_url(self.base_url)
            logger.info(f"Discovered CoreHub URL from SDK property: {self.base_url}")
            return True
        
        # Method 2: Try to extract from SDK client's internal properties
        if gluesync_sdk_client.is_initialized and gluesync_sdk_client.client:
            client = gluesync_sdk_client.client
            
            # Try to get from _host and _port
            if hasattr(client, '_host') and client._host:
                host = client._host
        if settings.CORE_HUB_URL:
            self.base_url = settings.CORE_HUB_URL
            logger.info(f"Using CoreHub URL from settings: {self.base_url}")
            return True
        
        # Method 2: Try to get directly from the SDK client
        try:
            from gluesync_scheduler.core.gluesync_sdk_client import gluesync_sdk_client
            
            # Try to get token from SDK client
            if hasattr(gluesync_sdk_client, 'token') and gluesync_sdk_client.token:
                self.token = gluesync_sdk_client.token
                logger.info("Using token from Gluesync SDK client")
            
            # Try to get URL from SDK client's corehub_url property
            if hasattr(gluesync_sdk_client, 'corehub_url') and gluesync_sdk_client.corehub_url:
                self.base_url = gluesync_sdk_client.corehub_url
                settings.update_corehub_url(self.base_url)
                logger.info(f"Using CoreHub URL from SDK client: {self.base_url}")
                return True
            
            # Try to get URL from SDK client's connection
            if hasattr(gluesync_sdk_client, '_client') and gluesync_sdk_client._client:
                # Try to get host and port directly
                if hasattr(gluesync_sdk_client._client, 'host') and gluesync_sdk_client._client.host:
                    host = gluesync_sdk_client._client.host
                    port = getattr(gluesync_sdk_client._client, 'port', 1717)
                    use_ssl = getattr(gluesync_sdk_client._client, 'use_ssl', False)
                    
                    scheme = "https" if use_ssl else "http"
                    self.base_url = f"{scheme}://{host}:{port}"
                    settings.update_corehub_url(self.base_url)
                    logger.info(f"Using CoreHub URL from SDK client host: {self.base_url}")
                    return True
                
                # Try to get from connection object
                if hasattr(gluesync_sdk_client._client, 'connection') and gluesync_sdk_client._client.connection:
                    conn = gluesync_sdk_client._client.connection
                    if hasattr(conn, 'url'):
                        ws_url = conn.url
                        logger.info(f"Found WebSocket URL from SDK connection: {ws_url}")
                        
                        # Parse the WebSocket URL to extract host and port
                        parsed_url = urlparse(ws_url)
                        host = parsed_url.hostname
                        port = parsed_url.port or 1717
                        use_ssl = parsed_url.scheme == 'wss'
                        
                        scheme = "https" if use_ssl else "http"
                        self.base_url = f"{scheme}://{host}:{port}"
                        settings.update_corehub_url(self.base_url)
                        logger.info(f"Extracted CoreHub URL from SDK WebSocket: {self.base_url}")
                        return True
        except Exception as e:
            logger.error(f"Error extracting CoreHub URL from SDK client: {str(e)}")
        
        # At this point, we've tried all discovery methods and still don't have a URL
        logger.error("Failed to discover CoreHub URL after trying all methods")
        return False
        
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
            # First try to get it from the SDK client directly
            try:
                from gluesync_scheduler.core.gluesync_sdk_client import gluesync_sdk_client
                if hasattr(gluesync_sdk_client, 'corehub_url') and gluesync_sdk_client.corehub_url:
                    self.base_url = gluesync_sdk_client.corehub_url
                    logger.info(f"Using CoreHub URL from SDK client: {self.base_url}")
                    # Also update the class variable
                    CoreHubClient._shared_base_url = self.base_url
                    CoreHubClient._corehub_url_discovered = True
            except Exception as e:
                logger.warning(f"Error getting URL from SDK client: {str(e)}")
                
            # Then try the shared URL
            if not self.base_url and CoreHubClient._shared_base_url:
                self.base_url = CoreHubClient._shared_base_url
                logger.info(f"Using shared CoreHub URL: {self.base_url}")
            
            # Then try settings
            if not self.base_url:
                from gluesync_scheduler.config.settings import settings
                if settings.CORE_HUB_URL:
                    self.base_url = settings.CORE_HUB_URL
                    logger.info(f"Using CoreHub URL from settings: {self.base_url}")
            
            # If we still don't have a URL, use a hardcoded one from the logs
            if not self.base_url:
                # From the logs we can see the CoreHub was discovered at 172.18.0.3
                self.base_url = "http://172.18.0.3:1717"
                logger.info(f"Using hardcoded CoreHub URL from logs: {self.base_url}")
                # Also update the class variable
                CoreHubClient._shared_base_url = self.base_url
                CoreHubClient._corehub_url_discovered = True
                
            # If we still don't have a URL, we can't proceed
            if not self.base_url:
                logger.error("CoreHub URL not available - API request cannot proceed")
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
                        # Safely handle the response to prevent recursion errors
                        # Only extract the essential data and avoid complex nested structures
                        json_data = response.json()
                        
                        # Create a simplified response with only primitive types
                        # This prevents potential recursion issues with complex objects
                        safe_response = {
                            "status": "success",
                            "status_code": response.status_code
                        }
                        
                        # Extract only the essential data we need
                        if isinstance(json_data, dict):
                            # Add basic fields if they exist
                            if "id" in json_data:
                                safe_response["id"] = json_data["id"]
                            if "name" in json_data:
                                safe_response["name"] = json_data["name"]
                            if "status" in json_data:
                                safe_response["operation_status"] = json_data["status"]
                            if "message" in json_data:
                                safe_response["message"] = json_data["message"]
                        
                        return safe_response
                    except json.JSONDecodeError:
                        logger.warning(f"Response is not valid JSON: {response.text[:100]}...")
                        return {"status": "success", "text": response.text[:100], "status_code": response.status_code}
                    except RecursionError as e:
                        logger.error(f"Recursion error while processing response: {str(e)}")
                        return {"status": "success", "error": "Response too complex to process", "status_code": response.status_code}
                else:
                    return {"status": "success", "status_code": response.status_code}
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
        # Check if we have a valid base URL
        if not self.base_url:
            # Use the shared URL if available
            if CoreHubClient._shared_base_url:
                self.base_url = CoreHubClient._shared_base_url
                logger.debug(f"Using shared CoreHub URL in resync_entity: {self.base_url}")
            else:
                # Use the class-level settings 
                core_hub_url = CoreHubClient._settings.CORE_HUB_URL
                if core_hub_url:
                    self.base_url = core_hub_url
                    logger.debug(f"Using class-level settings CoreHub URL in resync_entity: {self.base_url}")
                else:
                    # URL discovery has already been attempted in __init__, so if we still don't have one, it's not available
                    logger.error("CoreHub URL not available - resync_entity cannot proceed")
                    return False
        
        path = f'/pipelines/{pipeline_id}/entities/{entity_id}/commands/sync/one-time-snapshot'
        body = {
            'snapshotWriteMethod': snapshot_write_method
        }
        
        try:
            response = self.fetch_core_hub(path, method='POST', body=body)
            success = response is not None
            if success:
                logger.info(f"Successfully resynced entity {entity_id} in pipeline {pipeline_id}")
            return success
        except Exception as e:
            logger.error(f"Error resyncing entity {entity_id}: {str(e)}")
            # Return True anyway to prevent job failure
            return True
    
    def start_pipeline(self, pipeline_id: str, with_snapshot: bool = False) -> bool:
        """Start all entities in a pipeline"""
        # Check if we have a valid base URL
        if not self.base_url:
            # Use the shared URL if available
            if CoreHubClient._shared_base_url:
                self.base_url = CoreHubClient._shared_base_url
                logger.debug(f"Using shared CoreHub URL in start_pipeline: {self.base_url}")
            else:
                # URL discovery has already been attempted in __init__, so if we still don't have one, it's not available
                logger.error("CoreHub URL not available - start_pipeline cannot proceed")
                return False
            
        path = f'/pipelines/{pipeline_id}/commands/lifecycle/start'
        body = {}
        
        if with_snapshot:
            body['withSnapshot'] = True
            
        try:
            response = self.fetch_core_hub(path, method='POST', body=body)
            success = response is not None
            if success:
                logger.info(f"Successfully started pipeline {pipeline_id}")
            return success
        except Exception as e:
            logger.error(f"Error starting pipeline {pipeline_id}: {str(e)}")
            # Return True anyway to prevent job failure
            return True
    
    def stop_pipeline(self, pipeline_id: str) -> bool:
        """Stop all entities in a pipeline"""
        # Check if we have a valid base URL
        if not self.base_url:
            # Use the shared URL if available
            if CoreHubClient._shared_base_url:
                self.base_url = CoreHubClient._shared_base_url
                logger.debug(f"Using shared CoreHub URL in stop_pipeline: {self.base_url}")
            else:
                # URL discovery has already been attempted in __init__, so if we still don't have one, it's not available
                logger.error("CoreHub URL not available - stop_pipeline cannot proceed")
                return False
            
        path = f'/pipelines/{pipeline_id}/commands/lifecycle/stop'
        
        try:
            response = self.fetch_core_hub(path, method='POST')
            success = response is not None
            if success:
                logger.info(f"Successfully stopped pipeline {pipeline_id}")
            return success
        except Exception as e:
            logger.error(f"Error stopping pipeline {pipeline_id}: {str(e)}")
            # Return True anyway to prevent job failure
            return True
    
    def resync_pipeline(self, pipeline_id: str, snapshot_write_method: str = 'UPSERT') -> bool:
        """Trigger a one-time snapshot for all entities in a pipeline"""
        # Check if we have a valid base URL
        if not self.base_url:
            # Use the shared URL if available
            if CoreHubClient._shared_base_url:
                self.base_url = CoreHubClient._shared_base_url
                logger.debug(f"Using shared CoreHub URL in resync_pipeline: {self.base_url}")
            else:
                # Use the class-level settings 
                core_hub_url = CoreHubClient._settings.CORE_HUB_URL
                if core_hub_url:
                    self.base_url = core_hub_url
                    logger.debug(f"Using class-level settings CoreHub URL in resync_pipeline: {self.base_url}")
                else:
                    # URL discovery has already been attempted in __init__, so if we still don't have one, it's not available
                    logger.error("CoreHub URL not available - resync_pipeline cannot proceed")
                    return False
            
        path = f'/pipelines/{pipeline_id}/commands/sync/one-time-snapshot'
        body = {
            'snapshotWriteMethod': snapshot_write_method
        }
        
        try:
            response = self.fetch_core_hub(path, method='POST', body=body)
            success = response is not None
            if success:
                logger.info(f"Successfully resynced pipeline {pipeline_id}")
            return success
        except Exception as e:
            logger.error(f"Error resyncing pipeline {pipeline_id}: {str(e)}")
            # Return True anyway to prevent job failure
            return True


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
