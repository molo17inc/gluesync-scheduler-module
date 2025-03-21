#!/usr/bin/env python3
"""
 * This file is part of Gluesync Scheduler Module.
 *
 * Gluesync Scheduler Module is dual-licensed under the following licenses:
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

from config import settings

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("gluesync-sdk-client")

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
        scheme = "https" if self._client.use_ssl else "http"
        
        if host and port:
            return f"{scheme}://{host}:{port}"
        return None
    
    async def initialize(self):
        """Initialize the Gluesync client"""
        if self._is_initialized:
            logger.info("Gluesync SDK client already initialized")
            return
        
        # Parse host and port from CORE_HUB_URL if provided
        host = None
        port = None
        use_discovery = True
        
        if settings.CORE_HUB_URL:
            parsed_url = urlparse(settings.CORE_HUB_URL)
            host = parsed_url.hostname
            port = parsed_url.port
            use_discovery = False
            logger.info(f"Using provided CoreHub URL: {settings.CORE_HUB_URL}")
        else:
            logger.info("No CoreHub URL provided, will use UDP discovery")
        
        # Get license file path from settings
        license_file_path = settings.GLUESYNC_LICENSE_FILE
        if not os.path.exists(license_file_path):
            logger.warning(f"License file not found at {license_file_path}, will attempt to proceed without it")
        
        # SSL configuration
        use_ssl = settings.GLUESYNC_USE_SSL
        if settings.CORE_HUB_URL and parsed_url.scheme == "https":
            use_ssl = True
        keystore_path = settings.GLUESYNC_KEYSTORE_PATH
        keystore_password = settings.GLUESYNC_KEYSTORE_PASSWORD
        
        try:
            # Create the client
            self._client = GluesyncClient(
                host=host,  # None will trigger UDP discovery
                port=port,  # None will use default port
                license_file_path=license_file_path,
                module_tag=settings.GLUESYNC_MODULE_TAG,
                ssl=use_ssl,
                keystore_path=keystore_path,
                keystore_password=keystore_password
            )
            
            # Set up event handlers
            self._client.on_connected = self._on_connected
            self._client.on_disconnected = self._on_disconnected
            self._client.on_error = self._on_error
            
            # Connect to CoreHub
            if host and port:
                logger.info(f"Connecting to CoreHub at {host}:{port}...")
            else:
                logger.info("Starting UDP discovery to find CoreHub...")
            await self._client.connect()
            
            self._is_initialized = True
            logger.info("Gluesync SDK client initialized successfully")
            
        except GluesyncLicenseError as e:
            logger.error(f"License error: {e}")
            raise
            
        except GluesyncAuthenticationError as e:
            logger.error(f"Authentication error: {e}")
            raise
            
        except GluesyncConnectionError as e:
            logger.error(f"Connection error: {e}")
            raise
            
        except GluesyncError as e:
            logger.error(f"Gluesync error: {e}")
            raise
            
        except Exception as e:
            logger.exception(f"Unexpected error: {e}")
            raise
    
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
