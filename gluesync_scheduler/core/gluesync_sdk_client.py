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
import ssl
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
    def corehub_url(self) -> str:
        """Get the CoreHub URL after discovery"""
        if not self._client or not self._is_initialized:
            return None
            
        # Get the host and port from the client
        host = self._client.host
        port = self._client.port
        use_ssl = self._client.use_ssl
        
        # Use the helper method to build the URL
        return self._build_corehub_url(host, port, use_ssl)
        
    def _build_corehub_url(self, host, port, use_ssl=False):
        """Build a proper CoreHub URL with the given host, port and SSL setting
        
        Args:
            host (str): The host name or IP address
            port (int): The port number
            use_ssl (bool): Whether to use HTTPS or HTTP
            
        Returns:
            str: The formatted CoreHub URL
        """
        if not host:
            return None
            
        # Use default port if None
        if port is None:
            port = 1717
            
        scheme = "https" if use_ssl else "http"
        return f"{scheme}://{host}:{port}"
    
    async def initialize(self):
        """Initialize the Gluesync client with indefinite retries and exponential backoff
        
        The method will retry indefinitely with exponential backoff starting at 1 second,
        doubling each time up to 30 seconds, then resetting back to 1 second.
        """
        if self._is_initialized:
            logger.info("Gluesync SDK client already initialized")
            return
        
        # Parse host and port from CORE_HUB_URL if provided
        host = None
        port = None
        use_discovery = True
        parsed_url = None
        
        if settings.CORE_HUB_URL:
            parsed_url = urlparse(settings.CORE_HUB_URL)
            host = parsed_url.hostname
            port = parsed_url.port
            use_discovery = False
            logger.info(f"Using provided CoreHub host: {host} at port: {port}")
        else:
            logger.info("No CoreHub URL provided, will use UDP discovery instead")
        
        # Get license file path from settings
        license_file_path = settings.GLUESYNC_LICENSE_FILE
        if not os.path.exists(license_file_path):
            logger.warning(f"License file not found at {license_file_path}, will attempt to proceed without it")
        
        # SSL configuration - sync with web server settings
        use_ssl = settings.SSL_ENABLED or settings.GLUESYNC_USE_SSL
        
        # If CORE_HUB_URL is https://, force SSL
        if settings.CORE_HUB_URL and parsed_url and parsed_url.scheme == "https":
            use_ssl = True
            logger.info("Enforcing SSL because CoreHub URL uses HTTPS scheme")
        
        # If certificate files exist, force SSL
        cert_file = os.getenv('SSL_CERT_FILE')
        key_file = os.getenv('SSL_KEY_FILE')
        if cert_file and os.path.exists(cert_file) and key_file and os.path.exists(key_file):
            use_ssl = True
            logger.info(f"Enforcing SSL because certificate files exist: {cert_file} and {key_file}")
        
        # Security configuration
        security_config = settings.GLUESYNC_SECURITY_CONFIG
        if security_config and os.path.exists(security_config):
            logger.info(f"Using security config from: {security_config}")
            use_ssl = True
        elif security_config:
            logger.warning(f"Security config file not found at {security_config}, will use default settings")
            security_config = None
            
        # Create the client
        self._client = GluesyncClient(
            host=host,  # None will trigger UDP discovery
            port=port if port is not None else 1717,  # Use default port 1717 if None
            license_file_path=license_file_path,
            module_tag=settings.GLUESYNC_MODULE_TAG,
            use_ssl=use_ssl,
            security_config=security_config,
            verify_ssl=not settings.SSL_SKIP_VERIFY,
        )
        
        # Set up event handlers
        self._client.on_connected = self._on_connected
        self._client.on_disconnected = self._on_disconnected
        self._client.on_error = self._on_error
        
        # Connect to CoreHub with indefinite retry logic and exponential backoff
        retry_count = 0
        backoff_delay = 1  # Start with 1 second delay
        max_backoff = 30  # Maximum backoff of 30 seconds
        cycle_count = 0   # Count full cycles of backoff
        
        while True:  # Retry indefinitely
            try:
                if host and port:
                    logger.info(f"Connecting to CoreHub at {self._build_corehub_url(host, port, use_ssl)}...")
                    await self._client.connect()
                    break  # Connection successful
                else:
                    # UDP discovery mode
                    if retry_count > 0:
                        logger.info(f"Retry {retry_count} (cycle {cycle_count}) for UDP discovery...")
                    else:
                        logger.info("Starting UDP discovery to find CoreHub...")
                    
                    await self._client.connect()
                    
                    # After connect, check if we have a host (discovery worked)
                    if self._client.host:
                        logger.info(f"UDP discovery successful! Found CoreHub at {self._client.host}:{self._client.port}")
                        # Update the discovered host/port for future use
                        host = self._client.host
                        port = self._client.port
                        # Update the CoreHub URL in settings
                        corehub_url = self._build_corehub_url(host, port, use_ssl)
                        if corehub_url:
                            settings.update_corehub_url(corehub_url)
                            logger.info(f"Updated CoreHub URL to {corehub_url}")
                        break  # Connection successful
                    else:
                        # If no host was discovered, raise an error to trigger retry
                        raise GluesyncConnectionError("UDP discovery did not find a CoreHub")
                    
            except GluesyncConnectionError as e:
                if host and port:
                    # If we have a specific host/port and can't connect, don't retry
                    logger.error(f"Failed to connect to CoreHub at {self._build_corehub_url(host, port, use_ssl)}: {e}")
                    raise
                else:
                    # For UDP discovery, retry with exponential backoff
                    retry_count += 1
                    logger.warning(f"UDP discovery attempt {retry_count} failed: {e}")
                    
                    # Calculate backoff with exponential increase
                    logger.info(f"Waiting {backoff_delay} seconds before next retry...")
                    await asyncio.sleep(backoff_delay)
                    
                    # Double the backoff for next time, up to the maximum
                    backoff_delay = min(backoff_delay * 2, max_backoff)
                    
                    # If we've reached max backoff, reset on the next failure
                    if backoff_delay >= max_backoff:
                        backoff_delay = 1  # Reset to 1 second
                        cycle_count += 1   # Increment cycle count
                        logger.info(f"Completed backoff cycle {cycle_count}, resetting delay to 1 second")
                        
            except (GluesyncLicenseError, GluesyncAuthenticationError) as e:
                # Don't retry for these errors
                logger.error(f"{type(e).__name__}: {e}")
                raise
        
        self._is_initialized = True
        logger.info("Gluesync SDK client initialized successfully")
    
    async def shutdown(self):
        """Shutdown the Gluesync client"""
        if self._client and self._client.is_connected:
            logger.info("Disconnecting from CoreHub...")
            await self._client.disconnect()
            self._is_initialized = False
            self._token = None
    
    async def _on_connected(self, token):
        """
        Handle the connected event.
        
        Args:
            token: The JWT token received from the server
        """
        self._token = token
        logger.info(f"Connected to CoreHub successfully! Token received.")
    
    async def _on_disconnected(self, reason):
        """
        Handle the disconnected event.
        
        Args:
            reason: The reason for disconnection
        """
        self._token = None
        self._is_initialized = False
        logger.info(f"Disconnected from CoreHub: {reason}")
    
    async def _on_error(self, error):
        """
        Handle the error event.
        
        Args:
            error: The exception that occurred
        """
        logger.error(f"Error in connection: {error}")
        
# Create a global instance for easy import
gluesync_sdk_client = GluesyncSDKClient.get_instance()
