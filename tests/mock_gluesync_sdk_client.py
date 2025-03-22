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

"""
Mock implementation of the gluesync_sdk_client module for testing.
This allows tests to run without requiring the actual SDK to be installed.
"""

import logging
from tests.mock_gluesync_sdk import sdk

# Create a logger for the mock SDK client
logger = logging.getLogger("mock_gluesync_sdk_client")

class GluesyncSDKClient:
    """Mock implementation of the GluesyncSDKClient class"""
    
    _instance = None
    _token = "mock-token-123456"
    _client = None
    _is_initialized = False
    
    @classmethod
    def get_instance(cls):
        """Get the singleton instance"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        # This should not be called directly, use get_instance instead
        self._host = "mock-corehub"
        self._port = 8080
        self._use_ssl = False
        logger.info("Mock GluesyncSDKClient instance created")
    
    @property
    def token(self) -> str:
        """Get the current JWT token"""
        return self._token
    
    @property
    def client(self):
        """Get the Gluesync client instance"""
        return self._client
    
    @property
    def is_initialized(self) -> bool:
        """Check if the client is initialized"""
        return self._is_initialized
    
    @property
    def corehub_url(self) -> str:
        """Get the CoreHub URL after discovery"""
        return self._build_corehub_url(self._host, self._port, self._use_ssl)
    
    def _build_corehub_url(self, host, port, use_ssl=False):
        """Build a proper CoreHub URL with the given host, port and SSL setting"""
        protocol = "https" if use_ssl else "http"
        return f"{protocol}://{host}:{port}"
    
    def initialize(self, **kwargs):
        """Mock initialization method"""
        logger.info("Mock GluesyncSDKClient initializing with args: %s", kwargs)
        self._is_initialized = True
        
        # Create a mock client
        from tests.mock_gluesync_sdk import GluesyncClient
        self._client = GluesyncClient()
        self._client.host = self._host
        self._client.port = self._port
        self._client.use_ssl = self._use_ssl
        
        return True
    
    def connect(self, **kwargs):
        """Mock connect method"""
        logger.info("Mock GluesyncSDKClient connected with args: %s", kwargs)
        return True
    
    def disconnect(self):
        """Mock disconnect method"""
        logger.info("Mock GluesyncSDKClient disconnected")
        return True
    
    def shutdown(self):
        """Mock shutdown method"""
        logger.info("Mock GluesyncSDKClient shutting down")
        self._is_initialized = False
        self._client = None
        return True

# Create a singleton instance
gluesync_sdk_client = GluesyncSDKClient.get_instance()
