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
import logging.handlers
import os
import ssl
from typing import Optional

from gluesync_sdk import (
    GluesyncClient,
    GluesyncError,
    GluesyncConnectionError,
    GluesyncAuthenticationError,
    GluesyncLicenseError
)

from .path_resolver import resolve_gluesync_file
from .url_utils import normalize_corehub_host, DEFAULT_COREHUB_PORT

# Configure logging
# Ensure timestamps are always included in logs, even when run as a standalone script
log_level = getattr(logging, os.getenv('LOG_LEVEL', 'INFO'), logging.INFO)
log_dir = os.getenv('LOG_DIR', './logs')
logging.basicConfig(
    level=log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            filename=os.path.join(log_dir, "sdk_client.log"),
            maxBytes=100 * 1024 * 1024,  # 100MB per file
            backupCount=9  # 9 backup files + current = 10 files total (1GB max)
        )
    ],
    force=True  # Apply even if the root logger is already configured
)
logger = logging.getLogger(__name__)

class GluesyncSDKClient:
    """Singleton class for managing the Gluesync SDK client connection"""
    
    _instance = None
    _token = None
    _client = None
    _is_initialized = False
    _initializing = False
    
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
        
        # Use the helper method to build the URL
        return self._build_corehub_url(host, port)
        
    def _build_corehub_url(self, host, port):
        """Build a proper CoreHub URL with the given host, port and SSL setting
        
        Args:
            host (str): The host name or IP address
            port (int): The port number
            
        Returns:
            str: The formatted CoreHub URL
        """
        if not host:
            return None
            
        # Use default port if None
        if port is None:
            port = 1717
            
        scheme = "https" if os.getenv('SSL_ENABLED', 'False').lower() in ('true', '1', 't') else "http"
        return f"{scheme}://{host}:{port}"
    
    async def initialize(self, force_reconnect: bool = False):
        """Initialize the Gluesync client with indefinite retries and exponential backoff

        The method will retry indefinitely with exponential backoff starting at 1 second,
        doubling each time up to 30 seconds, then resetting back to 1 second.

        Args:
            force_reconnect: When True, disconnect and discard the existing client
                so a fresh login handshake is performed. This is used by the 401
                retry path in CoreHubClient to recover from stale/invalidated tokens.
        """
        if force_reconnect:
            await self._force_reconnect_disconnect()
        elif self._initializing:
            logger.info("Gluesync SDK client is already initializing, skipping duplicate initialization call")
            return

        self._initializing = True
        try:
            if self._is_initialized and self._token:
                logger.info("Gluesync SDK client already initialized with valid token")
                return
            elif self._is_initialized and not self._token:
                logger.warning("SDK client marked as initialized but no token available - reinitializing")
                self._is_initialized = False

            # If client already exists and is active (connected or reconnecting), do not re-create it
            if self._client and (self._client.is_connected or getattr(self._client, '_reconnecting', False)):
                logger.info("SDK client already exists and is connected or reconnecting, skipping recreation")
                if self._client.is_connected and not self._token:
                    self._token = self._client.token
                    if self._token:
                        self._is_initialized = True
                return

            ssl_enabled = os.getenv('SSL_ENABLED', 'False').lower() in ('true', '1', 't')
            ssl_skip_verify = os.getenv('SSL_SKIP_VERIFY', 'False').lower() in ('true', '1', 't')
            logger.info(f"SSL is {'enabled' if ssl_enabled else 'disabled'}")

            host, port = self._resolve_host_and_port(ssl_enabled)
            license_file_path = self._resolve_license_file()
            security_config = self._resolve_security_config(ssl_enabled)

            client_args = self._build_client_args(
                host, port, ssl_enabled, ssl_skip_verify,
                license_file_path, security_config,
            )

            self._client = GluesyncClient(**client_args)
            self._setup_event_handlers()

            protocol = 'https' if ssl_enabled else 'http'
            await self._connect_with_retry(host, port, protocol)

            await self._wait_for_token()
        finally:
            self._initializing = False

    async def _force_reconnect_disconnect(self):
        """Disconnect and clear the existing client to force a fresh login."""
        if self._client:
            try:
                if self._client.is_connected:
                    logger.info("Force-reconnect: disconnecting existing SDK client...")
                    await self._client.disconnect()
            except Exception as e:
                logger.warning(f"Force-reconnect: error during disconnect: {e}")
            self._client = None
        self._token = None
        self._is_initialized = False
        self._initializing = False

    def _resolve_host_and_port(self, ssl_enabled):
        """Parse host and port from GLUESYNC_HOST, falling back to UDP discovery."""
        host = None
        port = None
        gluesync_host = os.getenv('GLUESYNC_HOST', '')
        if not gluesync_host:
            logger.info("No CoreHub URL provided, will use UDP discovery instead")
            return host, port

        logger.info(f"GLUESYNC_HOST environment variable set to: {gluesync_host}")
        normalized_host, normalized_port, normalized_url = normalize_corehub_host(
            gluesync_host, ssl_enabled, DEFAULT_COREHUB_PORT,
        )
        if normalized_host:
            host = normalized_host
            port = normalized_port
            if normalized_url and normalized_url != gluesync_host:
                os.environ['GLUESYNC_HOST'] = normalized_url
                logger.info(f"Normalized GLUESYNC_HOST to: {normalized_url}")
        else:
            logger.warning("Unable to parse GLUESYNC_HOST value, falling back to discovery")
        return host, port

    def _resolve_license_file(self):
        """Resolve the license file path, warning if not found."""
        license_file_path, license_exists = resolve_gluesync_file(
            'GLUESYNC_LICENSE_FILE', 'gs-license.dat'
        )
        if not license_exists:
            logger.warning(
                "License file not found at %s (including legacy fallbacks), will attempt to proceed without it",
                license_file_path,
            )
        return license_file_path

    def _resolve_security_config(self, ssl_enabled):
        """Resolve the security config path, only when SSL is enabled."""
        config_path, config_exists = resolve_gluesync_file(
            'GLUESYNC_SECURITY_CONFIG', 'security-config.json'
        )
        if ssl_enabled:
            if config_exists:
                logger.info(f"Using security config from: {config_path}")
                return config_path
            logger.warning(
                "Security config file not found at %s (including legacy fallbacks), will use default settings",
                config_path,
            )
            return None
        if config_exists:
            logger.info(
                "Security config found at %s but SSL is disabled - ignoring security config",
                config_path,
            )
        return None

    def _build_client_args(self, host, port, ssl_enabled, ssl_skip_verify,
                           license_file_path, security_config):
        """Build the GluesyncClient constructor arguments and log them."""
        protocol = 'https' if ssl_enabled else 'http'
        logger.info(f"Creating GluesyncClient with:")
        logger.info(f"  - host: {host}")
        logger.info(f"  - port: {port if port is not None else DEFAULT_COREHUB_PORT}")
        logger.info(f"  - protocol: {protocol}")
        logger.info(f"  - use_ssl: {ssl_enabled}")
        logger.info(f"  - verify_ssl: {not ssl_skip_verify}")
        logger.info(f"  - security_config: {security_config}")
        logger.info(f"  - module_tag: {os.getenv('GLUESYNC_MODULE_TAG', 'chronos')}")

        client_args = {
            'host': host,
            'port': port if port is not None else 1717,
            'license_file_path': license_file_path,
            'module_tag': os.getenv('GLUESYNC_MODULE_TAG', 'chronos'),
            'use_ssl': ssl_enabled,
            'security_config': security_config,
            'verify_ssl': not ssl_skip_verify,
            'ping_interval': 5.0,
            'timeout': 10.0,
            'discovery_start_port': 1717,
            'discovery_port_range': 10,
        }

        safe_args = client_args.copy()
        if safe_args.get('security_config'):
            safe_args['security_config'] = '[REDACTED]'
        logger.info(f"Initializing GluesyncClient with args: {safe_args}")
        return client_args

    def _setup_event_handlers(self):
        """Attach connection event handlers to the SDK client."""
        self._client.on_connected = self._on_connected
        self._client.on_disconnected = self._on_disconnected
        self._client.on_error = self._on_error

    async def _connect_with_retry(self, host, port, protocol):
        """Connect to CoreHub with indefinite retry and exponential backoff."""
        retry_count = 0
        backoff_delay = 1
        max_backoff = 30
        cycle_count = 0

        while True:
            try:
                if host and port:
                    await self._connect_direct(host, port, protocol, retry_count, cycle_count)
                    break
                else:
                    await self._connect_discovery(retry_count, cycle_count)
                    host = self._client.host
                    port = self._client.port
                    break
            except GluesyncConnectionError as e:
                retry_count += 1
                logger.warning(f"Connection attempt {retry_count} failed: {e}")
                logger.info(f"Waiting {backoff_delay} seconds before next retry...")
                await asyncio.sleep(backoff_delay)
                backoff_delay = min(backoff_delay * 2, max_backoff)
                if backoff_delay >= max_backoff:
                    backoff_delay = 1
                    cycle_count += 1
                    logger.info(f"Completed backoff cycle {cycle_count}, resetting delay to 1 second")
            except (GluesyncLicenseError, GluesyncAuthenticationError) as e:
                logger.exception(f"{type(e).__name__}: {e}")
                raise

    async def _connect_direct(self, host, port, protocol, retry_count, cycle_count):
        """Connect using a known host and port."""
        if retry_count == 0:
            logger.info(f"Connecting to CoreHub at {protocol}://{host}:{port}...")
        else:
            logger.info(f"Retry {retry_count} (cycle {cycle_count}) connecting to CoreHub at {protocol}://{host}:{port}...")
        await self._client.connect()

    async def _connect_discovery(self, retry_count, cycle_count):
        """Connect using UDP discovery."""
        if retry_count > 0:
            logger.info(f"Retry {retry_count} (cycle {cycle_count}) for UDP discovery...")
        else:
            logger.info("Starting UDP discovery to find CoreHub...")
        await self._client.connect()
        if self._client.host:
            logger.info(f"UDP discovery successful! Found CoreHub at {self._client.host}:{self._client.port}")
            corehub_url = self._build_corehub_url(self._client.host, self._client.port)
            logger.info(f"Updated CoreHub URL to {corehub_url}")
        else:
            raise GluesyncConnectionError("UDP discovery did not find a CoreHub")

    async def _wait_for_token(self):
        """Wait for the SDK token to arrive after connection, with a timeout."""
        connection_timeout = 10
        token_check_interval = 0.5
        total_wait = 0

        while total_wait < connection_timeout and not self._token:
            await asyncio.sleep(token_check_interval)
            total_wait += token_check_interval
            logger.debug(f"Waiting for token... ({total_wait}s/{connection_timeout}s)")

        if self._token:
            self._is_initialized = True
            logger.info("Gluesync SDK client initialized successfully with token")
        else:
            self._is_initialized = False
            logger.error("SDK client connected but no token received within timeout")
            raise GluesyncAuthenticationError("No authentication token received after connection")
    
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
        if token:
            self._token = token
            self._is_initialized = True  # Mark as initialized when connected
            logger.info(f"Connected to CoreHub successfully! Token received and stored.")
            logger.debug(f"Token length: {len(token) if token else 0} characters")
        else:
            logger.error("Connected to CoreHub but no token provided in callback")
            self._token = None
            self._is_initialized = False
    
    async def _on_disconnected(self, reason):
        """
        Handle the disconnected event.
        
        Args:
            reason: The reason for disconnection
        """
        logger.warning(f"Disconnected from CoreHub: {reason}")
        
        # Clear authentication state
        old_token = self._token
        self._token = None
        self._is_initialized = False
        
        if old_token:
            logger.info("Previous authentication token invalidated")
        
        # Note: The SDK handles reconnection internally, so we don't need to start a reconnection task
        logger.info("SDK will handle reconnection automatically")
    
    async def _on_error(self, error):
        """
        Handle the error event.
        
        Args:
            error: The exception that occurred
        """
        logger.error(f"Error in connection: {error}")
        
        # Reset token and initialization state
        self._token = None
        self._is_initialized = False
        
        # Note: The SDK handles reconnection internally, so we don't need custom reconnection logic
        logger.info("Connection error occurred - SDK will handle reconnection automatically")
    
    # Note: Callback methods for on_reconnecting, on_reconnected, and on_token_updated
    # have been removed as they are not supported by the current SDK version.
    # The SDK handles reconnection internally and updates tokens through the on_connected callback.
        
    # Note: Custom reconnection methods have been removed as the SDK handles
    # reconnection internally. The SDK will automatically attempt to reconnect
    # when the connection is lost and will call the on_connected callback
    # when reconnection is successful.
        
# Create a global instance for easy import
gluesync_sdk_client = GluesyncSDKClient.get_instance()
