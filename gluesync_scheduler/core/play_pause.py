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
import os
from typing import Dict, List, Optional, Any, Union
from datetime import datetime

import requests
from urllib.parse import quote_plus, urlparse, urljoin
import uuid

from gluesync_scheduler.config.settings import settings
from gluesync_scheduler.core.gluesync_sdk_client import gluesync_sdk_client
from gluesync_scheduler.services.group_service import group_service

# Configure logging
# Ensure timestamps are always included in logs, even when run as a standalone script
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(settings.LOG_DIR if hasattr(settings, 'LOG_DIR') else './logs', 
                                        "play_pause.log"))
    ],
    force=True  # Apply even if the root logger is already configured
)
logger = logging.getLogger(__name__)


class CoreHubClient:
    """Client for interacting with the Gluesync Core Hub API"""
    
    # Singleton instance
    _instance = None
    
    # Shared discovered URL to persist across method calls
    _discovered_url = None
    
    def __new__(cls):
        """Implement singleton pattern"""
        if cls._instance is None:
            logger.info("Creating new CoreHubClient singleton instance")
            cls._instance = super(CoreHubClient, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, provided_url=None):
        """Initialize the Core Hub client with configuration from settings
        
        Args:
            provided_url: Optional explicitly provided URL that takes highest priority
        """
        # Skip initialization if already done (singleton pattern)
        if getattr(self, '_initialized', False):
            # If a URL is provided to an already initialized instance, update it
            if provided_url is not None:
                self.base_url = provided_url
                CoreHubClient._discovered_url = provided_url
                logger.debug(f"Updating singleton base URL to: {provided_url}")
            return
        
        logger.info("Initializing CoreHubClient")
        
        # Initialize core properties
        self.base_url = provided_url  # Use provided URL if available
        self.token = None
        self.entity_start_timeout = settings.ENTITY_START_TIMEOUT
        
        # If no URL was explicitly provided, try discovery options
        if self.base_url is None:
            self._initialize_corehub_url()
        else:
            # Store explicitly provided URL in class variable
            CoreHubClient._discovered_url = self.base_url
            logger.info(f"Using provided CoreHub URL: {self.base_url}")
        
        # Get the token once we have a valid base URL
        if self.base_url:
            self._initialize_token()
        
        # Mark as initialized to avoid duplicate initialization
        self._initialized = True
        
    def _initialize_corehub_url(self):
        """Initialize the CoreHub URL for API requests
        
        The URL is determined using the following priority order:
        1. settings.GLUESYNC_HOST
        2. Previously discovered URL
        3. URL from SDK client 
        4. Default URL (localhost:1717)
        """
        # Priority 1: Use the URL from settings
        if settings.GLUESYNC_HOST:
            self.base_url = settings.GLUESYNC_HOST
            # Ensure the URL has the correct protocol based on SSL settings
            if not self.base_url.startswith(('http://', 'https://')):
                protocol = 'https' if settings.SSL_ENABLED else 'http'
                self.base_url = f"{protocol}://{self.base_url}"
            # Store in class variable for future use
            CoreHubClient._discovered_url = self.base_url
            logger.info(f"Using CoreHub URL from settings: {self.base_url}")
            return
        
        # Priority 2: Use the previously discovered URL
        if CoreHubClient._discovered_url is not None:
            self.base_url = CoreHubClient._discovered_url
            logger.debug(f"Using previously discovered CoreHub URL: {self.base_url}")
            return
            
        # Priority 3: Try to get URL from SDK client
        try:
            if gluesync_sdk_client and gluesync_sdk_client.is_initialized and gluesync_sdk_client.corehub_url:
                self.base_url = gluesync_sdk_client.corehub_url
                # Ensure the URL has the correct protocol based on SSL settings
                if not self.base_url.startswith(('http://', 'https://')):
                    protocol = 'https' if settings.SSL_ENABLED else 'http'
                    self.base_url = f"{protocol}://{self.base_url}"
                CoreHubClient._discovered_url = self.base_url
                logger.info(f"Using CoreHub URL from SDK client: {self.base_url}")
                return
        except Exception as e:
            logger.debug(f"Could not get URL from SDK client: {e}")
        
        # Priority 4: Use default URL for localhost
        # This is a fallback to ensure requests can still work within the same container
        protocol = "https" if settings.SSL_ENABLED else "http"
        default_url = f"{protocol}://localhost:1717"
        self.base_url = default_url
        CoreHubClient._discovered_url = self.base_url
        logger.info(f"Using default CoreHub URL: {self.base_url}")
        
        # Log SSL settings for debugging
        logger.info(f"SSL is {'enabled' if settings.SSL_ENABLED else 'disabled'}")
        logger.info(f"SSL_SKIP_VERIFY: {settings.SSL_SKIP_VERIFY}")
    
    def _initialize_token(self):
        """Initialize the token for API authentication"""
        try:
            from gluesync_scheduler.core.gluesync_sdk_client import gluesync_sdk_client
            if hasattr(gluesync_sdk_client, 'token') and gluesync_sdk_client.token:
                self.token = gluesync_sdk_client.token
                logger.info("Using authentication token from SDK client")
                return True
        except Exception as e:
            logger.warning(f"Could not get token from SDK client: {str(e)}")
        
        logger.warning("No authentication token available - API calls may fail")
        return False
        
    async def _initialize_sdk(self):
        """Initialize the SDK client if not already initialized"""
        try:
            logger.info("Initializing Gluesync SDK client...")
            await gluesync_sdk_client.initialize()
            logger.info("Gluesync SDK client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Gluesync SDK client: {e}")
            logger.warning("Will attempt to continue without SDK initialization")
    
    # These methods are replaced by _initialize_corehub_url
        
    # This method is replaced by _initialize_token
            
    def _get_current_corehub_url(self):
        """Get the current CoreHub URL dynamically from the SDK or fallback to stored URL"""
        try:
            if gluesync_sdk_client and gluesync_sdk_client.is_initialized and gluesync_sdk_client.corehub_url:
                return gluesync_sdk_client.corehub_url
        except Exception as e:
            logger.debug(f"Could not get URL from SDK client: {e}")
        
        # Fallback to stored URL if SDK client is not available
        return self.base_url
    
    def _get_current_token(self):
        """Get the current authentication token dynamically from the SDK or fallback to stored token"""
        try:
            if gluesync_sdk_client:
                logger.info(f"SDK client available: {gluesync_sdk_client is not None}")
                logger.info(f"SDK client initialized: {gluesync_sdk_client.is_initialized if gluesync_sdk_client else 'N/A'}")
                
                if gluesync_sdk_client.is_initialized:
                    has_token_attr = hasattr(gluesync_sdk_client, 'token')
                    logger.info(f"SDK client has token attribute: {has_token_attr}")
                    
                    if has_token_attr:
                        current_token = gluesync_sdk_client.token
                        logger.info(f"SDK token available: {current_token is not None}")
                        if current_token:
                            logger.info(f"Successfully retrieved token from SDK: {current_token[:20]}...")
                            return current_token, True  # Return tuple: (token, from_sdk)
                        else:
                            logger.warning("SDK client token is None")
                    else:
                        logger.warning("SDK client does not have token attribute")
                else:
                    logger.warning("SDK client is not initialized")
            else:
                logger.warning("SDK client is not available")
        except Exception as e:
            logger.error(f"Error getting token from SDK client: {e}")
        
        # No fallback - if SDK is not properly connected, we should not use any token
        logger.error("SDK client not available or not initialized - no valid token available")
        logger.error("API requests will fail until SDK reconnects successfully")
        logger.error("Token retrieval result: FAILED (no token available)")
        return None, False  # Return tuple: (token, from_sdk)
    
    def fetch_core_hub(self, path: str, method: str = 'GET', body: Optional[Dict] = None, params: Optional[Dict] = None):
        """
        Make an HTTP request to the CoreHub API.
        
        Args:
            path: API endpoint path (e.g., '/pipelines')
            method: HTTP method (GET, POST, PUT, DELETE)
            body: Request body as dictionary
            params: URL parameters as dictionary
            
        Returns:
            Response data as dictionary or None if request failed
        """
        # Get the current URL dynamically from the SDK or fallback to stored URL
        current_url = self._get_current_corehub_url()
        if not current_url:
            logger.error("CoreHub URL not available - API request cannot proceed")
            logger.error("Please ensure CoreHub URL is configured before making API calls")
            return None
            
        # Get the current token dynamically from the SDK or fallback to stored token
        current_token, from_sdk = self._get_current_token()
        if from_sdk:
            logger.info(f"Token retrieval result: SUCCESS (from SDK)")
        elif current_token:
            logger.warning(f"Token retrieval result: FALLBACK (using cached token - may be outdated)")
        else:
            logger.error(f"Token retrieval result: FAILED (no token available)")
            
        if current_token:
            logger.info(f"Using token: {current_token[:20]}...{current_token[-10:] if len(current_token) > 30 else ''}")
        else:
            logger.warning("No authentication token available - proceeding with unauthenticated request")
            # Attempt SDK reconnection if not initialized
            if gluesync_sdk_client and not gluesync_sdk_client.is_initialized:
                logger.info("Attempting to reinitialize SDK client...")
                try:
                    import asyncio
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # Schedule reinitialization for later
                        asyncio.create_task(gluesync_sdk_client.initialize())
                        logger.info("SDK reinitialization scheduled")
                    else:
                        # Run sync initialization
                        loop.run_until_complete(gluesync_sdk_client.initialize())
                        logger.info("SDK reinitialization completed")
                        # Retry token retrieval
                        current_token, from_sdk = self._get_current_token()
                        if current_token:
                            logger.info("Token retrieved after SDK reinitialization")
                except Exception as reinit_error:
                    logger.error(f"Failed to reinitialize SDK: {reinit_error}")
            
        url = f"{current_url}{path}"
        headers = {
            'Authorization': f'Bearer {current_token}' if current_token else None,
            'Content-Type': 'application/json'
        }

        # Log request details if in debug mode
        if settings.DEBUG:
            logger.debug(f"Sending request to: {url}")
            logger.debug(f"Method: {method}")
            logger.debug(f"Headers: {headers}")
            logger.debug(f"Body: {body}")
            logger.debug(f"Params: {params}")
        
        # Determine SSL verification settings based on SSL_SKIP_VERIFY
        verify = not settings.SSL_SKIP_VERIFY if url.startswith('https://') else True
        if url.startswith('https://') and not verify:
            logger.info(f"SSL verification disabled for request to {url}")
            # Suppress insecure request warnings
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        try:
            # Make the request with SSL verification setting
            if method == 'GET':
                response = requests.get(url, headers=headers, params=params, verify=verify)
            elif method == 'POST':
                response = requests.post(url, headers=headers, json=body, params=params, verify=verify)
            elif method == 'PUT':
                response = requests.put(url, headers=headers, json=body, params=params, verify=verify)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, json=body, params=params, verify=verify)
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
            elif response.status_code == 401:
                # Authentication error - token is likely invalid
                error_message = "Authentication failed: Invalid or expired token"
                try:
                    # Try to parse the error message from the response
                    error_data = response.json()
                    if "message" in error_data:
                        error_message = error_data["message"]
                except Exception:
                    pass
                
                logger.error(f"Authentication error (401): {error_message}")
                
                # Reset token and trigger reconnection
                self.token = None
                # Note: SDK handles reconnection automatically when authentication fails
                # No manual intervention needed - the SDK will reconnect and get a new token
                logger.info("Authentication failed - SDK will handle reconnection automatically")
                
                # Return a specific error response for auth errors
                return {
                    "status": "error",
                    "status_code": 401,
                    "error": "authentication_failed",
                    "message": error_message
                }
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
    
    def start_entity(self, pipeline_id: str, entity_id: str, with_snapshot: bool = False, snapshot_write_method: str = 'UPSERT') -> bool:
        """Start a specific entity in a pipeline"""
        path = f'/pipelines/{pipeline_id}/commands/sync/start'
        params = {
            'entity': entity_id,
            'withSnapshot': 'true' if with_snapshot else 'false',
            'snapshotWriteMethod': snapshot_write_method
        }
            
        response = self.fetch_core_hub(path, method='POST', params=params)
        
        # Check for auth errors specifically
        if response and isinstance(response, dict) and response.get('status') == 'error':
            logger.error(f"Error starting entity {entity_id}: {response.get('message')}")
            return False
            
        return response is not None
    
    def stop_entity(self, pipeline_id: str, entity_id: str) -> bool:
        """Stop a specific entity in a pipeline"""
        path = f'/pipelines/{pipeline_id}/commands/sync/stop'
        params = {'entity': entity_id}
        response = self.fetch_core_hub(path, method='POST', params=params)
        
        # Check for auth errors specifically
        if response and isinstance(response, dict) and response.get('status') == 'error':
            logger.error(f"Error stopping entity {entity_id}: {response.get('message')}")
            return False
            
        return response is not None
    
    def resync_entity(self, pipeline_id: str, entity_id: str, snapshot_write_method: str = 'UPSERT') -> bool:
        """Trigger a one-time snapshot for a specific entity"""
        # First, stop the entity to ensure data consistency during snapshot
        logger.info(f"Stopping entity {entity_id} before snapshot...")
        stop_success = self.stop_entity(pipeline_id, entity_id)
        if not stop_success:
            logger.error(f"Failed to stop entity {entity_id} before snapshot")
            return False
        
        # Wait a moment to ensure the entity is fully stopped
        import time
        time.sleep(5)
        
        # Now perform the snapshot
        path = f'/pipelines/{pipeline_id}/commands/sync/one-time-snapshot'
        params = {
            'entity': entity_id,
            'snapshotWriteMethod': snapshot_write_method
        }
        
        try:
            response = self.fetch_core_hub(path, method='POST', params=params)
            
            # Check for auth errors specifically
            if response and isinstance(response, dict) and response.get('status') == 'error':
                logger.error(f"Error resyncing entity {entity_id}: {response.get('message')}")
                return False
                
            success = response is not None
            if success:
                logger.info(f"Successfully resynced entity {entity_id} in pipeline {pipeline_id}")
            else:
                logger.error(f"Failed to resync entity {entity_id} in pipeline {pipeline_id}")
            return success
        except Exception as e:
            logger.error(f"Error resyncing entity {entity_id}: {str(e)}")
            # Don't hide errors anymore
            return False
    
    def start_pipeline(self, pipeline_id: str, with_snapshot: bool = False, snapshot_write_method: str = 'UPSERT') -> bool:
        """Start all entities in a pipeline"""
        path = f'/pipelines/{pipeline_id}/commands/sync/start'
        params = {
            'withSnapshot': 'true' if with_snapshot else 'false',
            'snapshotWriteMethod': snapshot_write_method
        }
            
        try:
            response = self.fetch_core_hub(path, method='POST', params=params)
            
            # Check for auth errors specifically
            if response and isinstance(response, dict) and response.get('status') == 'error':
                logger.error(f"Error starting pipeline {pipeline_id}: {response.get('message')}")
                return False
                
            success = response is not None
            if success:
                logger.info(f"Successfully started pipeline {pipeline_id}")
            else:
                logger.error(f"Failed to start pipeline {pipeline_id}")
            return success
        except Exception as e:
            logger.error(f"Error starting pipeline {pipeline_id}: {str(e)}")
            # Don't hide errors anymore
            return False
    
    def stop_pipeline(self, pipeline_id: str) -> bool:
        """Stop all entities in a pipeline"""
        path = f'/pipelines/{pipeline_id}/commands/sync/stop'
        
        try:
            response = self.fetch_core_hub(path, method='POST')
            
            # Check for auth errors specifically
            if response and isinstance(response, dict) and response.get('status') == 'error':
                logger.error(f"Error stopping pipeline {pipeline_id}: {response.get('message')}")
                return False
                
            success = response is not None
            if success:
                logger.info(f"Successfully stopped pipeline {pipeline_id}")
            else:
                logger.error(f"Failed to stop pipeline {pipeline_id}")
            return success
        except Exception as e:
            logger.error(f"Error stopping pipeline {pipeline_id}: {str(e)}")
            # Don't hide errors anymore
            return False
    
    def resync_pipeline(self, pipeline_id: str, snapshot_write_method: str = 'UPSERT') -> bool:
        """Trigger a one-time snapshot for all entities in a pipeline"""
        # First, stop the pipeline to ensure data consistency during snapshot
        logger.info(f"Stopping pipeline {pipeline_id} before snapshot...")
        stop_success = self.stop_pipeline(pipeline_id)
        if not stop_success:
            logger.error(f"Failed to stop pipeline {pipeline_id} before snapshot")
            return False
        
        # Wait a moment to ensure the pipeline is fully stopped
        import time
        time.sleep(5)
        
        # Now perform the snapshot
        path = f'/pipelines/{pipeline_id}/commands/sync/one-time-snapshot'
        params = {
            'snapshotWriteMethod': snapshot_write_method
        }
        
        try:
            response = self.fetch_core_hub(path, method='POST', params=params)
            
            # Check for auth errors specifically
            if response and isinstance(response, dict) and response.get('status') == 'error':
                logger.error(f"Error resyncing pipeline {pipeline_id}: {response.get('message')}")
                return False
                
            success = response is not None
            if success:
                logger.info(f"Successfully resynced pipeline {pipeline_id}")
            else:
                logger.error(f"Failed to resync pipeline {pipeline_id}")
            return success
        except Exception as e:
            logger.error(f"Error resyncing pipeline {pipeline_id}: {str(e)}")
            # Don't hide errors anymore
            return False
    
    def start_group(self, pipeline_id: str, group_id: str, with_snapshot: bool = False, snapshot_write_method: str = 'UPSERT') -> bool:
        """Start all entities in a specific group within a pipeline"""
        path = f'/pipelines/{pipeline_id}/commands/sync/start-group'
        params = {
            'groupId': group_id,
            'withSnapshot': 'true' if with_snapshot else 'false',
            'snapshotWriteMethod': snapshot_write_method
        }
            
        try:
            response = self.fetch_core_hub(path, method='POST', params=params)
            
            # Check for auth errors specifically
            if response and isinstance(response, dict) and response.get('status') == 'error':
                logger.error(f"Error starting group {group_id} in pipeline {pipeline_id}: {response.get('message')}")
                return False
                
            success = response is not None
            if success:
                logger.info(f"Successfully started group {group_id} in pipeline {pipeline_id}")
            else:
                logger.error(f"Failed to start group {group_id} in pipeline {pipeline_id}")
            return success
        except Exception as e:
            logger.error(f"Error starting group {group_id} in pipeline {pipeline_id}: {str(e)}")
            return False
    
    def stop_group(self, pipeline_id: str, group_id: str) -> bool:
        """Stop all entities in a specific group within a pipeline"""
        path = f'/pipelines/{pipeline_id}/commands/sync/stop-group'
        params = {
            'groupId': group_id
        }
        
        try:
            response = self.fetch_core_hub(path, method='POST', params=params)
            
            # Check for auth errors specifically
            if response and isinstance(response, dict) and response.get('status') == 'error':
                logger.error(f"Error stopping group {group_id} in pipeline {pipeline_id}: {response.get('message')}")
                return False
                
            success = response is not None
            if success:
                logger.info(f"Successfully stopped group {group_id} in pipeline {pipeline_id}")
            else:
                logger.error(f"Failed to stop group {group_id} in pipeline {pipeline_id}")
            return success
        except Exception as e:
            logger.error(f"Error stopping group {group_id} in pipeline {pipeline_id}: {str(e)}")
            return False
    
    def resync_group(self, pipeline_id: str, group_id: str, snapshot_write_method: str = 'UPSERT') -> bool:
        """Trigger a one-time snapshot for all entities in a specific group within a pipeline"""
        # First, stop the group to ensure data consistency during snapshot
        logger.info(f"Stopping group {group_id} before snapshot...")
        stop_success = self.stop_group(pipeline_id, group_id)
        if not stop_success:
            logger.error(f"Failed to stop group {group_id} before snapshot")
            return False
        
        # Wait a moment to ensure the group is fully stopped
        import time
        time.sleep(5)
        
        # Now perform the snapshot
        path = f'/pipelines/{pipeline_id}/commands/sync/one-time-snapshot-group'
        params = {
            'groupId': group_id,
            'snapshotWriteMethod': snapshot_write_method
        }
        
        try:
            response = self.fetch_core_hub(path, method='POST', params=params)
            
            # Check for auth errors specifically
            if response and isinstance(response, dict) and response.get('status') == 'error':
                logger.error(f"Error resyncing group {group_id} in pipeline {pipeline_id}: {response.get('message')}")
                return False
                
            success = response is not None
            if success:
                logger.info(f"Successfully resynced group {group_id} in pipeline {pipeline_id}")
            else:
                logger.error(f"Failed to resync group {group_id} in pipeline {pipeline_id}")
            return success
        except Exception as e:
            logger.error(f"Error resyncing group {group_id} in pipeline {pipeline_id}: {str(e)}")
            return False


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
    
    async def play_groups(self, pipeline_id: str, group_ids: List[str], with_snapshot: bool = False) -> bool:
        """Start all entities in specific groups within a pipeline
        
        Args:
            pipeline_id: Pipeline ID
            group_ids: List of group IDs to start
            with_snapshot: Start entities with snapshot
            
        Returns:
            bool: True if all operations were successful, False otherwise
        """
        try:
            logger.info(f"Starting groups {group_ids} in pipeline {pipeline_id} (with_snapshot={with_snapshot})")
            
            # Get all entity IDs for the specified groups
            entity_ids = await group_service.get_multiple_groups_entities(pipeline_id, group_ids)
            
            if not entity_ids:
                logger.warning(f"No entities found in groups {group_ids} for pipeline {pipeline_id}")
                return True  # Consider this successful since there's nothing to start
            
            logger.info(f"Found {len(entity_ids)} entities in groups {group_ids}: {entity_ids}")
            
            # Start the entities using existing entity-level functionality
            return await self.play_entities(pipeline_id, entity_ids, with_snapshot)
            
        except Exception as e:
            logger.error(f"Error starting groups {group_ids} in pipeline {pipeline_id}: {str(e)}")
            return False
    
    async def pause_groups(self, pipeline_id: str, group_ids: List[str]) -> bool:
        """Stop all entities in specific groups within a pipeline
        
        Args:
            pipeline_id: Pipeline ID
            group_ids: List of group IDs to stop
            
        Returns:
            bool: True if all operations were successful, False otherwise
        """
        try:
            logger.info(f"Stopping groups {group_ids} in pipeline {pipeline_id}")
            
            # Get all entity IDs for the specified groups
            entity_ids = await group_service.get_multiple_groups_entities(pipeline_id, group_ids)
            
            if not entity_ids:
                logger.warning(f"No entities found in groups {group_ids} for pipeline {pipeline_id}")
                return True  # Consider this successful since there's nothing to stop
            
            logger.info(f"Found {len(entity_ids)} entities in groups {group_ids}: {entity_ids}")
            
            # Stop the entities using existing entity-level functionality
            return await self.pause_entities(pipeline_id, entity_ids)
            
        except Exception as e:
            logger.error(f"Error stopping groups {group_ids} in pipeline {pipeline_id}: {str(e)}")
            return False
    
    async def resync_groups(self, pipeline_id: str, group_ids: List[str], snapshot_write_method: str = 'UPSERT') -> bool:
        """Trigger a one-time snapshot for all entities in specific groups within a pipeline
        
        Args:
            pipeline_id: Pipeline ID
            group_ids: List of group IDs to resync
            snapshot_write_method: Write method for the snapshot (default: UPSERT)
            
        Returns:
            bool: True if all operations were successful, False otherwise
        """
        try:
            logger.info(f"Resyncing groups {group_ids} in pipeline {pipeline_id} (method={snapshot_write_method})")
            
            # Get all entity IDs for the specified groups
            entity_ids = await group_service.get_multiple_groups_entities(pipeline_id, group_ids)
            
            if not entity_ids:
                logger.warning(f"No entities found in groups {group_ids} for pipeline {pipeline_id}")
                return True  # Consider this successful since there's nothing to resync
            
            logger.info(f"Found {len(entity_ids)} entities in groups {group_ids}: {entity_ids}")
            
            # Resync the entities using existing entity-level functionality
            return await self.resync_entities(pipeline_id, entity_ids, snapshot_write_method)
            
        except Exception as e:
            logger.error(f"Error resyncing groups {group_ids} in pipeline {pipeline_id}: {str(e)}")
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
