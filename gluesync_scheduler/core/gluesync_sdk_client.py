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

import asyncio
import logging
import os
import json
from pathlib import Path
from urllib.parse import urlparse
from typing import Optional

from gluesync_sdk import (
    GluesyncClient,
    GluesyncError,
    GluesyncConnectionError,
    GluesyncAuthenticationError,
    GluesyncLicenseError
)

from gluesync_scheduler.config.settings import settings

# Configure logging
logger = logging.getLogger(__name__)

class GluesyncSDKClient:
    """Singleton class for managing the Gluesync SDK client connection"""
    
    _instance = None
    _token = None
    _client = None
    _is_initialized = False
    _corehub_url = None
    
    @classmethod
    def get_instance(cls):
        """Get the singleton instance"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @property
    def token(self) -> Optional[str]:
        """Get the current JWT token"""
        return self._token
    
    @property
    def client(self) -> Optional[GluesyncClient]:
        """Get the Gluesync client instance"""
        return self._client
    
    @property
    def is_initialized(self) -> bool:
        """Check if the client is initialized"""
        return self._is_initialized
    
    @property
    def corehub_url(self) -> Optional[str]:
        """Get the CoreHub URL after discovery"""
        return self._corehub_url
    
    def _build_corehub_url(self, host, port, use_ssl=False):
        """
        Build a proper CoreHub URL with the given host, port and SSL setting
        
        Args:
            host (str): The host name or IP address
            port (int): The port number
            use_ssl (bool): Whether to use HTTPS or HTTP
            
        Returns:
            str: The formatted CoreHub URL
        """
        scheme = "https" if use_ssl else "http"
        return f"{scheme}://{host}:{port}"
    
    async def initialize(self):
        """
        Initialize the Gluesync client with indefinite retries and exponential backoff
        
        The method will retry indefinitely with exponential backoff starting at 1 second,
        doubling each time up to 30 seconds, then resetting back to 1 second.
        """
        if self._is_initialized:
            logger.info("Gluesync SDK client already initialized")
            return
        
        logger.info("Initializing Gluesync SDK client...")
        
        # Load security config if available
        ssl_config = {}
        if settings.GLUESYNC_USE_SSL and settings.GLUESYNC_SECURITY_CONFIG:
            try:
                if os.path.exists(settings.GLUESYNC_SECURITY_CONFIG):
                    with open(settings.GLUESYNC_SECURITY_CONFIG, 'r') as f:
                        security_config = json.load(f)
                    
                    # Extract SSL configuration
                    if 'ssl' in security_config:
                        ssl_config = {
                            'use_ssl': True,
                            'certificate_path': security_config['ssl'].get('sslCertificatePath'),
                            'certificate_password': security_config['ssl'].get('certificatePassword')
                        }
                        logger.info(f"Loaded SSL configuration from {settings.GLUESYNC_SECURITY_CONFIG}")
                else:
                    logger.warning(f"Security config file not found: {settings.GLUESYNC_SECURITY_CONFIG}")
            except Exception as e:
                logger.error(f"Error loading security config: {str(e)}")
        
        # Create the Gluesync client
        try:
            # Check if the license file exists
            if not os.path.exists(settings.GLUESYNC_LICENSE_FILE):
                logger.warning(f"License file not found: {settings.GLUESYNC_LICENSE_FILE}")
                
            # Initialize the client with the correct parameters
            self._client = GluesyncClient(
                license_file_path=settings.GLUESYNC_LICENSE_FILE,
                module_tag=settings.GLUESYNC_MODULE_TAG,
                **ssl_config
            )
            
            # Register event handlers
            self._client.on_connected = self._on_connected
            self._client.on_disconnected = self._on_disconnected
            self._client.on_error = self._on_error
            
            # Connect with retry logic
            retry_delay = 1  # Start with 1 second delay
            max_delay = 30   # Maximum delay of 30 seconds
            
            while True:
                try:
                    logger.info("Attempting to connect to Gluesync...")
                    await self._client.connect()
                    
                    # If we get here, connection was successful
                    self._is_initialized = True
                    logger.info("Gluesync SDK client initialized successfully")
                    
                    # Get CoreHub URL from the connection
                    try:
                        # First try to get host directly from the client
                        if hasattr(self._client, '_host') and self._client._host:
                            host = self._client._host
                            port = getattr(self._client, '_port', 1717)  # Default to 1717 if not available
                            use_ssl = getattr(self._client, '_use_ssl', False)
                            
                            if host and port:
                                self._corehub_url = self._build_corehub_url(host, port, use_ssl)
                                logger.info(f"Discovered CoreHub URL: {self._corehub_url}")
                        # If that fails, try to get it from the discovery result
                        elif hasattr(self._client, '_discovery_result') and self._client._discovery_result:
                            host = self._client._discovery_result.get('host')
                            port = self._client._discovery_result.get('port', 1717)
                            use_ssl = self._client._discovery_result.get('ssl', False)
                            
                            if host and port:
                                self._corehub_url = self._build_corehub_url(host, port, use_ssl)
                                logger.info(f"Discovered CoreHub URL from discovery result: {self._corehub_url}")
                        # If all else fails, use the default CoreHub URL from settings if available
                        elif settings.CORE_HUB_URL:
                            self._corehub_url = settings.CORE_HUB_URL
                            logger.info(f"Using CoreHub URL from settings: {self._corehub_url}")
                    except Exception as e:
                        logger.error(f"Error during CoreHub discovery: {str(e)}")
                    
                    break
                except GluesyncLicenseError as e:
                    logger.error(f"License error: {str(e)}")
                    logger.error(f"License file path: {settings.GLUESYNC_LICENSE_FILE}")
                    logger.error("Please check your license file and restart the application.")
                    raise  # License errors are fatal, no retry
                except (GluesyncConnectionError, GluesyncAuthenticationError) as e:
                    logger.warning(f"Connection error: {str(e)}")
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    
                    # Exponential backoff with reset
                    retry_delay = min(retry_delay * 2, max_delay)
                    if retry_delay == max_delay:
                        # Reset back to 1 second after reaching max delay
                        retry_delay = 1
                except Exception as e:
                    logger.error(f"Unexpected error during initialization: {str(e)}")
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    
                    # Exponential backoff with reset
                    retry_delay = min(retry_delay * 2, max_delay)
        except Exception as e:
            logger.error(f"Failed to create Gluesync client: {str(e)}")
            raise
    
    async def shutdown(self):
        """Shutdown the Gluesync client"""
        if self._client and self._is_initialized:
            logger.info("Shutting down Gluesync SDK client...")
            try:
                await self._client.disconnect()
                self._is_initialized = False
                self._token = None
                logger.info("Gluesync SDK client shut down successfully")
            except Exception as e:
                logger.error(f"Error shutting down Gluesync SDK client: {str(e)}")
    
    def _on_connected(self, token):
        """
        Handle the connected event.
        
        Args:
            token: The JWT token received from the server
        """
        self._token = token
        logger.info("Connected to Gluesync server")
        logger.info(f"Received token: {token[:10]}...")
    
    def _on_disconnected(self, reason):
        """
        Handle the disconnected event.
        
        Args:
            reason: The reason for disconnection
        """
        logger.warning(f"Disconnected from Gluesync server: {reason}")
        self._token = None
        
        # Attempt to reconnect if not shutting down
        if self._is_initialized:
            logger.info("Attempting to reconnect...")
            asyncio.create_task(self._reconnect())
    
    async def _reconnect(self):
        """Attempt to reconnect to the Gluesync server"""
        retry_delay = 1  # Start with 1 second delay
        max_delay = 30   # Maximum delay of 30 seconds
        
        while self._is_initialized:
            try:
                logger.info("Attempting to reconnect to Gluesync...")
                await self._client.connect()
                
                # If we get here, reconnection was successful
                logger.info("Reconnected to Gluesync server")
                break
            except Exception as e:
                logger.warning(f"Reconnection failed: {str(e)}")
                logger.info(f"Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
                
                # Exponential backoff with reset
                retry_delay = min(retry_delay * 2, max_delay)
                if retry_delay == max_delay:
                    # Reset back to 1 second after reaching max delay
                    retry_delay = 1
    
    def _on_error(self, error):
        """
        Handle the error event.
        
        Args:
            error: The exception that occurred
        """
        logger.error(f"Gluesync SDK error: {str(error)}")
        
# Create a global instance for easy import
gluesync_sdk_client = GluesyncSDKClient.get_instance()
