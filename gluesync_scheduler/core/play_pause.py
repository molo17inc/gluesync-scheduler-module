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

from gluesync_scheduler.core.gluesync_sdk_client import gluesync_sdk_client
from gluesync_scheduler.services.group_service import group_service


_SUCCESS_JSON_FIELDS = ("id", "name", "message")



def _copy_success_payload_fields(safe, json_data):
    """Copy known scalar fields and list payloads onto the sanitized success dict."""
    for key in _SUCCESS_JSON_FIELDS:
        if key in json_data:
            safe[key] = json_data[key]
    if "status" in json_data:
        safe["operation_status"] = json_data["status"]
    for key in ("entities", "statuses", "data", "items"):
        if isinstance(json_data.get(key), list):
            safe[key] = json_data[key]


def _build_success_response(response):
    """Build the safe success dict returned to callers of ``fetch_core_hub``.

    Kept as a module-level helper so both the initial and retry code paths
    can share it without inflating the caller's cognitive complexity.
    """
    if not response.text:
        return {"status": "success", "status_code": response.status_code}

    try:
        json_data = response.json()
    except json.JSONDecodeError:
        logger.warning(f"Response is not valid JSON: {response.text[:100]}...")
        return {
            "status": "success",
            "text": response.text[:100],
            "status_code": response.status_code,
        }
    except RecursionError:
        logger.exception("Recursion error while processing response")
        return {
            "status": "success",
            "error": "Response too complex to process",
            "status_code": response.status_code,
        }

    safe = {"status": "success", "status_code": response.status_code}
    if isinstance(json_data, list):
        # Preserve list payloads (entities-status, config/entities). Command
        # callers still get status/status_code; GET unwrap code reads entities.
        safe["entities"] = json_data
        return safe
    if isinstance(json_data, dict):
        _copy_success_payload_fields(safe, json_data)
    return safe


def _extract_error_message(response, default):
    """Best-effort extraction of ``message`` from a JSON error response."""
    try:
        data = response.json()
    except Exception:
        return default
    if isinstance(data, dict) and "message" in data:
        return data["message"]
    return default

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
            filename=os.path.join(log_dir, "play_pause.log"),
            maxBytes=100 * 1024 * 1024,  # 100MB per file
            backupCount=9  # 9 backup files + current = 10 files total (1GB max)
        )
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
        self.entity_start_timeout = int(os.getenv('ENTITY_START_TIMEOUT', '2'))
        
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
        gluesync_host = os.getenv('GLUESYNC_HOST', '')
        if gluesync_host:
            self.base_url = gluesync_host
            # Ensure the URL has the correct protocol based on SSL settings
            ssl_enabled = os.getenv('SSL_ENABLED', 'False').lower() in ('true', '1', 't')
            if not self.base_url.startswith(('http://', 'https://')):
                protocol = 'https' if ssl_enabled else 'http'
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
                ssl_enabled = os.getenv('SSL_ENABLED', 'False').lower() in ('true', '1', 't')
                if not self.base_url.startswith(('http://', 'https://')):
                    protocol = 'https' if ssl_enabled else 'http'
                    self.base_url = f"{protocol}://{self.base_url}"
                CoreHubClient._discovered_url = self.base_url
                logger.info(f"Using CoreHub URL from SDK client: {self.base_url}")
                return
        except Exception as e:
            logger.debug(f"Could not get URL from SDK client: {e}")
        
        # Priority 4: Use default URL for localhost
        # This is a fallback to ensure requests can still work within the same container
        ssl_enabled = os.getenv('SSL_ENABLED', 'False').lower() in ('true', '1', 't')
        protocol = "https" if ssl_enabled else "http"
        default_url = f"{protocol}://localhost:1717"
        self.base_url = default_url
        CoreHubClient._discovered_url = self.base_url
        logger.info(f"Using default CoreHub URL: {self.base_url}")
        
        # Log SSL settings for debugging
        logger.info(f"SSL is {'enabled' if ssl_enabled else 'disabled'}")
        logger.info(f"SSL_SKIP_VERIFY: {os.getenv('SSL_SKIP_VERIFY', 'False').lower() in ('true', '1', 't')}")
    
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
        prepared = self._prepare_request(path, method, body, params)
        if prepared is None:
            return None
        url, headers, verify = prepared

        try:
            response = self._do_request(method, url, headers, body, params, verify)
            if response is None:
                return None
            return self._interpret_response(response, method, url, headers, body, params, verify)
        except Exception as e:
            logger.exception("Request failed with exception")
            return {
                "status": "error",
                "status_code": 500,
                "error": "exception",
                "message": str(e),
            }

    def _prepare_request(self, path, method, body, params):
        """Resolve URL, token, headers, SSL verify. Return (url, headers, verify) or None on fatal setup."""
        current_url = self._get_current_corehub_url()
        if not current_url:
            logger.error("CoreHub URL not available - API request cannot proceed")
            logger.error("Please ensure CoreHub URL is configured before making API calls")
            return None

        current_token = self._acquire_token()
        url = f"{current_url}{path}"
        headers = {
            'Authorization': f'Bearer {current_token}' if current_token else None,
            'Content-Type': 'application/json',
        }

        self._log_debug_request(url, method, headers, body, params)
        verify = self._resolve_verify(url)
        return url, headers, verify

    def _acquire_token(self):
        """Pull a token from the SDK, log its provenance, and attempt one reinit if missing."""
        current_token, from_sdk = self._get_current_token()
        if from_sdk:
            logger.info("Token retrieval result: SUCCESS (from SDK)")
        elif current_token:
            logger.warning("Token retrieval result: FALLBACK (using cached token - may be outdated)")
        else:
            logger.error("Token retrieval result: FAILED (no token available)")

        if current_token:
            tail = current_token[-10:] if len(current_token) > 30 else ''
            logger.info(f"Using token: {current_token[:20]}...{tail}")
            return current_token

        logger.warning("No authentication token available - proceeding with unauthenticated request")
        if gluesync_sdk_client and not gluesync_sdk_client.is_initialized:
            current_token = self._try_reinit_and_reacquire_token()
        return current_token

    @staticmethod
    def _try_reinit_and_reacquire_token():
        """Best-effort SDK reinit + token re-fetch. Returns the fresh token or None."""
        logger.info("Attempting to reinitialize SDK client...")
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(gluesync_sdk_client.initialize())
                logger.info("SDK reinitialization scheduled")
                return None
            loop.run_until_complete(gluesync_sdk_client.initialize())
            logger.info("SDK reinitialization completed")
        except Exception:
            logger.exception("Failed to reinitialize SDK")
            return None

        # Cannot call self._get_current_token from a staticmethod; caller retries via CoreHubClient path.
        # Return None here so the caller falls back to unauthenticated; subsequent 401 will trigger the
        # full refresh_and_retry flow.
        return None

    @staticmethod
    def _log_debug_request(url, method, headers, body, params):
        if os.getenv('DEBUG', 'False').lower() not in ('true', '1', 't'):
            return
        logger.debug(f"Sending request to: {url}")
        logger.debug(f"Method: {method}")
        logger.debug(f"Headers: {headers}")
        logger.debug(f"Body: {body}")
        logger.debug(f"Params: {params}")

    @staticmethod
    def _resolve_verify(url):
        """Return the ``verify`` value for `requests`, honouring SSL_SKIP_VERIFY on https URLs."""
        if not url.startswith('https://'):
            return True
        ssl_skip_verify = os.getenv('SSL_SKIP_VERIFY', 'False').lower() in ('true', '1', 't')
        if ssl_skip_verify:
            logger.info(f"SSL verification disabled for request to {url}")
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            return False
        return True

    def _interpret_response(self, response, method, url, headers, body, params, verify):
        """Turn a raw ``requests.Response`` into the standard result dict."""
        if response.status_code in (200, 201, 202, 204):
            return _build_success_response(response)
        if response.status_code == 401:
            return self._handle_unauthorized(response, method, url, headers, body, params, verify)
        logger.error(
            f"Request failed with status code {response.status_code}: {response.text}"
        )
        return {
            "status": "error",
            "status_code": response.status_code,
            "error": "request_failed",
            "message": response.text,
        }

    @staticmethod
    def _do_request(method, url, headers, body, params, verify):
        """Dispatch a single HTTP call using `requests`. Returns None on unsupported method."""
        method = method.upper()
        if method == 'GET':
            return requests.get(url, headers=headers, params=params, verify=verify)
        if method == 'POST':
            return requests.post(url, headers=headers, json=body, params=params, verify=verify)
        if method == 'PUT':
            return requests.put(url, headers=headers, json=body, params=params, verify=verify)
        if method == 'DELETE':
            return requests.delete(url, headers=headers, json=body, params=params, verify=verify)
        logger.error(f"Unsupported HTTP method: {method}")
        return None

    def _handle_unauthorized(self, response, method, url, headers, body, params, verify):
        """Handle a 401 from CoreHub: optionally refresh the SDK token and retry once."""
        self._last_status_code = 401
        error_message = _extract_error_message(
            response, default="Authentication failed: Invalid or expired token",
        )
        logger.error(f"Authentication error (401): {error_message}")

        refresh_enabled = os.getenv(
            'CHRONOS_SDK_TOKEN_REFRESH_ON_401', 'True',
        ).lower() in ('true', '1', 't')

        if refresh_enabled:
            retry_result = self._refresh_and_retry(method, url, headers, body, params, verify)
            if retry_result is not None:
                return retry_result

        # Reset token and let the SDK reconnect on next call.
        self.token = None
        logger.info("Authentication failed - SDK will handle reconnection automatically")
        return {
            "status": "error",
            "status_code": 401,
            "error": "authentication_failed",
            "message": error_message,
        }

    def _refresh_and_retry(self, method, url, headers, body, params, verify):
        """Force an SDK re-login and retry the original request exactly once.

        Returns the retry result dict, or None if refresh/retry was not attempted
        (in which case the caller should fall through to the standard 401 response).
        """
        logger.info(
            "CHRONOS_SDK_TOKEN_REFRESH_ON_401 is enabled. "
            "Triggering SDK token refresh and single retry..."
        )
        try:
            self._reinitialize_sdk_client()
            new_token, _from_sdk = self._get_current_token()
            if not new_token:
                logger.error("Failed to retrieve a fresh token after SDK client reinitialization")
                return None

            logger.info("Successfully refreshed SDK token after 401. Retrying request...")
            retry_headers = headers.copy()
            retry_headers['Authorization'] = f'Bearer {new_token}'
            retry_response = self._do_request(method, url, retry_headers, body, params, verify)
            if retry_response is None:
                return None
            return self._process_retry_response(retry_response)
        except Exception:
            logger.exception("Error during SDK token refresh or retry")
            return None

    @staticmethod
    def _reinitialize_sdk_client():
        """Reset the shared SDK client so the next call performs a fresh login handshake."""
        gluesync_sdk_client._is_initialized = False
        gluesync_sdk_client._token = None
        if gluesync_sdk_client._client:
            gluesync_sdk_client._client.connected = False

        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            loop.run_until_complete(gluesync_sdk_client.initialize())
        else:
            asyncio.run(gluesync_sdk_client.initialize())

    def _process_retry_response(self, retry_response):
        """Convert a retry response into the standard result dict."""
        if retry_response.status_code in (200, 201, 202, 204):
            logger.info("Retry successful after token refresh!")
            return _build_success_response(retry_response)

        if retry_response.status_code == 401:
            self._last_status_code = 401
            detail = _extract_error_message(
                retry_response,
                default="Authentication failed again on retry with refreshed token",
            )
            logger.error(f"Authentication error (401) on retry: {detail}")
            return {
                "status": "error",
                "status_code": 401,
                "error": "authentication_failed",
                "message": (
                    "CoreHub rejected the refreshed token; check chronos credentials "
                    f"/ CoreHub session TTL. Details: {detail}"
                ),
            }

        logger.error(
            f"Retry request failed with status code {retry_response.status_code}: "
            f"{retry_response.text}"
        )
        return {
            "status": "error",
            "status_code": retry_response.status_code,
            "error": "request_failed",
            "message": retry_response.text,
        }
    
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

    def get_pipeline_entities_status(self, pipeline_id: str) -> List[Dict[str, Any]]:
        """Get the real-time runtime status of every entity in a pipeline.

        Calls the CoreHub ``GET /pipelines/{pipeline_id}/entities-status`` endpoint,
        which returns the same status data the MPP UI uses to render Active / Hold /
        Error / Warning. Each entry carries ``isSyncActive``, ``isMigrationActive``,
        ``isBusy`` and ``errorState`` flags.

        Returns:
            A list of entity status dicts, or an empty list if the request failed.
        """
        response = self.fetch_core_hub(f'/pipelines/{pipeline_id}/entities-status')
        if not response:
            return []
        if isinstance(response, list):
            return response
        if isinstance(response, dict):
            for key in ('entities', 'statuses', 'data', 'items'):
                if isinstance(response.get(key), list):
                    return response[key]
            return [response]
        return []

    @staticmethod
    def _entity_status_id(entry: Dict[str, Any]) -> Optional[str]:
        """Best-effort extraction of the entity identifier from a status entry."""
        for key in ('entityId', 'entityID', 'id', 'entity_id'):
            value = entry.get(key)
            if value:
                return str(value)
        return None

    @staticmethod
    def _has_nonempty_state(entry: Dict[str, Any], key: str) -> bool:
        """True when ``entry[key]`` is a non-null, non-empty payload."""
        if not isinstance(entry, dict):
            return False
        state = entry.get(key)
        if state is None:
            return False
        if isinstance(state, dict):
            return len(state) > 0
        return bool(state)

    @staticmethod
    def _entity_has_error(entry: Dict[str, Any]) -> bool:
        """Return True when ``errorState`` denotes an actual error or warning.

        Mirrors the CoreHub / MPP UI computation: an issue is present only when
        ``errorState`` is a non-null **and non-empty** object. An empty ``{}``
        (cleared issue) is treated as no error, matching the Kotlin MCP code.
        Hub !2219 stores WARNING vs ERROR on ``errorState.severity``.
        """
        return CoreHubClient._has_nonempty_state(entry, "errorState")

    @staticmethod
    def _error_state_severity(entry: Dict[str, Any]) -> Optional[str]:
        """Return ``errorState.severity`` uppercased, or None."""
        if not isinstance(entry, dict):
            return None
        state = entry.get("errorState")
        if not isinstance(state, dict):
            return None
        raw = state.get("severity")
        if isinstance(raw, str) and raw.strip():
            return raw.strip().upper()
        return None

    @staticmethod
    def _entity_has_warning(entry: Dict[str, Any]) -> bool:
        """Return True when Hub reports ``errorState.severity == WARNING``."""
        return CoreHubClient._error_state_severity(entry) == "WARNING"

    @staticmethod
    def _is_entity_settled(entry: Dict[str, Any]) -> bool:
        """Return True when redo may proceed for this entity.

        Hold (paused), Error, and Warning are acceptable. Warning and Error
        remain settled even if Hub still reports ``isSyncActive`` /
        ``isMigrationActive`` (Hub !2219: an entity can keep running while
        Warning, same as Error).
        """
        if not isinstance(entry, dict):
            return False
        if CoreHubClient._entity_has_error(entry) or CoreHubClient._entity_has_warning(entry):
            return True
        if entry.get("isSyncActive") or entry.get("isMigrationActive"):
            return False
        return True

    def _pending_settled_ids(self, statuses, target_ids):
        """Return target IDs that are missing or still Active (not yet settled)."""
        status_by_id = {}
        for entry in statuses:
            eid = self._entity_status_id(entry)
            if eid:
                status_by_id[eid] = entry
        pending = set()
        for eid in target_ids:
            entry = status_by_id.get(eid)
            if entry is None or not self._is_entity_settled(entry):
                pending.add(eid)
        return pending

    def _wait_targets_or_proceed(self, pipeline_id, entity_ids, timeout_error, missing_warning) -> bool:
        """Wait for Hold/Error/Warning, or continue without polling when IDs cannot be resolved."""
        if entity_ids:
            if not self._wait_for_entities_settled(pipeline_id, entity_ids):
                logger.error(timeout_error)
                return False
            return True
        logger.warning(missing_warning)
        return True

    def _wait_for_entities_settled(self, pipeline_id: str, entity_ids: List[str],
                                   timeout: Optional[float] = None,
                                   poll_interval: Optional[float] = None) -> bool:
        """Poll CoreHub until every entity in ``entity_ids`` is no longer Active.

        An entity is considered ready to proceed when it reports Hold (paused),
        Error, or Warning. Only the Active state (``isSyncActive`` or
        ``isMigrationActive`` true, with no error/warning) keeps the poller waiting.

        Args:
            pipeline_id: Pipeline hosting the entities.
            entity_ids: Entity IDs that must all leave the Active state.
            timeout: Maximum seconds to wait before giving up (defaults to
                ``CHRONOS_REDO_PAUSE_TIMEOUT`` or 60s).
            poll_interval: Seconds between status checks (defaults to
                ``CHRONOS_REDO_POLL_INTERVAL`` or 5s).

        Returns:
            True if all entities reached Hold/Error/Warning within the timeout, False otherwise.
        """
        if not entity_ids:
            return True

        if timeout is None:
            timeout = float(os.getenv('CHRONOS_REDO_PAUSE_TIMEOUT', '60'))
        if poll_interval is None:
            poll_interval = float(os.getenv('CHRONOS_REDO_POLL_INTERVAL', '5'))

        target_ids = {str(eid) for eid in entity_ids}
        deadline = time.time() + timeout
        last_pending: set = set(target_ids)

        logger.info(
            f"Waiting for {len(target_ids)} entity(ies) in pipeline {pipeline_id} "
            f"to leave the Active state (Hold, Error, or Warning accepted) "
            f"(timeout={timeout}s, poll={poll_interval}s)"
        )

        while time.time() < deadline:
            pending = self._pending_settled_ids(
                self.get_pipeline_entities_status(pipeline_id), target_ids
            )

            if not pending:
                logger.info(
                    f"All {len(target_ids)} target entity(ies) in pipeline "
                    f"{pipeline_id} are now settled (Hold, Error, or Warning)"
                )
                return True

            if pending != last_pending:
                logger.info(
                    f"Pipeline {pipeline_id}: {len(target_ids) - len(pending)}/"
                    f"{len(target_ids)} entities settled; still waiting on: "
                    f"{sorted(pending)}"
                )
                last_pending = pending

            time.sleep(poll_interval)

        logger.error(
            f"Timed out after {timeout}s waiting for entities to settle in pipeline "
            f"{pipeline_id}; still pending: {sorted(last_pending)}"
        )
        return False

    def _get_group_entity_ids(self, pipeline_id: str, group_id: str) -> List[str]:
        """Synchronously resolve the entity IDs that belong to a group.

        Mirrors ``GroupService.get_group_entities`` but stays synchronous so it
        can be used from the synchronous ``CoreHubClient.redo_group`` flow.
        """
        response = self.fetch_core_hub(f'/pipelines/{pipeline_id}/config/entities')
        if not response:
            return []
        entities = response if isinstance(response, list) else response.get('entities', [])
        if not isinstance(entities, list):
            return []
        group_entity_ids: List[str] = []
        for entity in entities:
            if isinstance(entity, dict) and entity.get('groupId') == group_id:
                entity_id = entity.get('id') or entity.get('entityId')
                if entity_id:
                    group_entity_ids.append(str(entity_id))
        return group_entity_ids

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
    
    def redo_entity(self, pipeline_id: str, entity_id: str, with_snapshot: bool = False, snapshot_write_method: str = 'UPSERT') -> bool:
        """Restart CDC for a specific entity after snapshot using redo command"""
        # Pause the entity first so the target is stopped before the redo is applied
        logger.info(f"Pausing entity {entity_id} before redo...")
        pause_success = self.stop_entity(pipeline_id, entity_id)
        if not pause_success:
            logger.error(f"Failed to pause entity {entity_id} before redo")
            return False

        # Poll CoreHub until the entity leaves the Active state (Hold or Error) before issuing the redo
        if not self._wait_for_entities_settled(pipeline_id, [entity_id]):
            logger.error(f"Entity {entity_id} did not leave the Active state before redo")
            return False

        path = f'/pipelines/{pipeline_id}/commands/sync/redo'
        params = {
            'entity': entity_id,
            'withSnapshot': 'true' if with_snapshot else 'false',
            'snapshotWriteMethod': snapshot_write_method
        }

        response = self.fetch_core_hub(path, method='POST', params=params)

        if response and isinstance(response, dict) and response.get('status') == 'error':
            logger.error(f"Error redoing entity {entity_id}: {response.get('message')}")
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
            
        response = self.fetch_core_hub(path, method='POST', params=params)
        
        # Check for auth errors specifically
        if response and isinstance(response, dict) and response.get('status') == 'error':
            logger.error(f"Error starting pipeline {pipeline_id}: {response.get('message')}")
            return False
            
        return response is not None
    
    def redo_pipeline(self, pipeline_id: str, with_snapshot: bool = False, snapshot_write_method: str = 'UPSERT') -> bool:
        """Trigger redo command to run snapshot then restart CDC for entire pipeline"""
        # Pause the pipeline first so the target is stopped before the redo is applied
        logger.info(f"Pausing pipeline {pipeline_id} before redo...")
        pause_success = self.stop_pipeline(pipeline_id)
        if not pause_success:
            logger.error(f"Failed to pause pipeline {pipeline_id} before redo")
            return False

        # Poll CoreHub until every entity in the pipeline leaves the Active state (Hold or Error)
        pipeline_entity_ids = [
            str(e.get('id') or e.get('entityId'))
            for e in self.get_pipeline_entities(pipeline_id)
            if isinstance(e, dict) and (e.get('id') or e.get('entityId'))
        ]
        if not self._wait_targets_or_proceed(
            pipeline_id,
            pipeline_entity_ids,
            f"Pipeline {pipeline_id} entities did not all leave the Active state before redo",
            f"Could not resolve entity IDs for pipeline {pipeline_id}; "
            f"proceeding with redo without status polling",
        ):
            return False

        path = f'/pipelines/{pipeline_id}/commands/sync/redo'
        params = {
            'withSnapshot': 'true' if with_snapshot else 'false',
            'snapshotWriteMethod': snapshot_write_method
        }

        try:
            response = self.fetch_core_hub(path, method='POST', params=params)

            if response and isinstance(response, dict) and response.get('status') == 'error':
                logger.error(f"Error redoing pipeline {pipeline_id}: {response.get('message')}")
                return False

            success = response is not None
            if success:
                logger.info(f"Successfully triggered redo for pipeline {pipeline_id}")
            else:
                logger.error(f"Failed to trigger redo for pipeline {pipeline_id}")
            return success
        except Exception as e:
            logger.error(f"Error redoing pipeline {pipeline_id}: {str(e)}")
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

    def redo_group(self, pipeline_id: str, group_id: str, with_snapshot: bool = False,
                   snapshot_write_method: str = 'UPSERT') -> bool:
        """Trigger redo command for all entities within a group"""
        # Pause the group first so the target is stopped before the redo is applied
        logger.info(f"Pausing group {group_id} in pipeline {pipeline_id} before redo...")
        pause_success = self.stop_group(pipeline_id, group_id)
        if not pause_success:
            logger.error(f"Failed to pause group {group_id} in pipeline {pipeline_id} before redo")
            return False

        # Poll CoreHub until every entity in the group leaves the Active state (Hold or Error)
        group_entity_ids = self._get_group_entity_ids(pipeline_id, group_id)
        if not self._wait_targets_or_proceed(
            pipeline_id,
            group_entity_ids,
            f"Group {group_id} entities in pipeline {pipeline_id} did not all "
            f"leave the Active state before redo",
            f"Could not resolve entity IDs for group {group_id} in pipeline "
            f"{pipeline_id}; proceeding with redo without status polling",
        ):
            return False

        path = f'/pipelines/{pipeline_id}/commands/sync/redo-group'
        params = {
            'groupId': group_id,
            'withSnapshot': 'true' if with_snapshot else 'false',
            'snapshotWriteMethod': snapshot_write_method
        }

        try:
            response = self.fetch_core_hub(path, method='POST', params=params)

            if response and isinstance(response, dict) and response.get('status') == 'error':
                logger.error(f"Error redoing group {group_id} in pipeline {pipeline_id}: {response.get('message')}")
                return False

            success = response is not None
            if success:
                logger.info(f"Successfully triggered redo for group {group_id} in pipeline {pipeline_id}")
            else:
                logger.error(f"Failed to trigger redo for group {group_id} in pipeline {pipeline_id}")
            return success
        except Exception as e:
            logger.error(f"Error redoing group {group_id} in pipeline {pipeline_id}: {str(e)}")
            return False
    
    def enter_maintenance_mode(self, pipeline_id: str) -> bool:
        """Enter maintenance mode for a pipeline"""
        path = f'/pipelines/{pipeline_id}/commands/maintenance/enter'
        
        try:
            response = self.fetch_core_hub(path, method='POST')
            
            if response and isinstance(response, dict) and response.get('status') == 'error':
                logger.error(f"Error entering maintenance mode for pipeline {pipeline_id}: {response.get('message')}")
                return False
                
            success = response is not None
            if success:
                logger.info(f"Successfully entered maintenance mode for pipeline {pipeline_id}")
            else:
                logger.error(f"Failed to enter maintenance mode for pipeline {pipeline_id}")
            return success
        except Exception as e:
            logger.error(f"Error entering maintenance mode for pipeline {pipeline_id}: {str(e)}")
            return False
    
    def exit_maintenance_mode(self, pipeline_id: str) -> bool:
        """Exit maintenance mode for a pipeline"""
        path = f'/pipelines/{pipeline_id}/commands/maintenance/exit'
        
        try:
            response = self.fetch_core_hub(path, method='POST')
            
            if response and isinstance(response, dict) and response.get('status') == 'error':
                logger.error(f"Error exiting maintenance mode for pipeline {pipeline_id}: {response.get('message')}")
                return False
                
            success = response is not None
            if success:
                logger.info(f"Successfully exited maintenance mode for pipeline {pipeline_id}")
            else:
                logger.error(f"Failed to exit maintenance mode for pipeline {pipeline_id}")
            return success
        except Exception as e:
            logger.error(f"Error exiting maintenance mode for pipeline {pipeline_id}: {str(e)}")
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
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.client.start_pipeline, pipeline_id, with_snapshot
        )
    
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
        loop = asyncio.get_running_loop()
        success = True
        
        for entity_id in entity_ids:
            logger.info(f"Starting entity {entity_id}")
            result = await loop.run_in_executor(
                None, self.client.start_entity, pipeline_id, entity_id, with_snapshot
            )
            if not result:
                logger.error(f"Failed to start entity {entity_id}")
                success = False
            
            # Wait a short time between entity operations to avoid overwhelming the Core Hub
            await asyncio.sleep(self.client.entity_start_timeout)
        
        return success

    async def redo_group(self, pipeline_id: str, group_id: str, with_snapshot: bool = False,
                         snapshot_write_method: str = 'UPSERT') -> bool:
        """Trigger redo for all entities in a specific group"""
        logger.info(
            f"Redo group {group_id} in pipeline {pipeline_id} (with_snapshot={with_snapshot}, snapshot_write_method={snapshot_write_method})"
        )
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.client.redo_group, pipeline_id, group_id, with_snapshot, snapshot_write_method
        )

    async def redo_groups(self, pipeline_id: str, group_ids: List[str], with_snapshot: bool = False,
                          snapshot_write_method: str = 'UPSERT') -> bool:
        """Trigger redo for multiple groups in a pipeline"""
        success = True

        for group_id in group_ids:
            logger.info(f"Redoing group {group_id}")
            result = await self.redo_group(pipeline_id, group_id, with_snapshot, snapshot_write_method)
            if not result:
                logger.error(f"Failed to redo group {group_id}")
                success = False

            await asyncio.sleep(self.client.entity_start_timeout)

        return success

    async def redo_pipeline(self, pipeline_id: str, with_snapshot: bool = False, snapshot_write_method: str = 'UPSERT') -> bool:
        """Trigger redo for all entities in a pipeline"""
        logger.info(
            f"Redo pipeline {pipeline_id} (with_snapshot={with_snapshot}, snapshot_write_method={snapshot_write_method})"
        )
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.client.redo_pipeline, pipeline_id, with_snapshot, snapshot_write_method
        )

    async def redo_entities(self, pipeline_id: str, entity_ids: List[str], with_snapshot: bool = False,
                            snapshot_write_method: str = 'UPSERT') -> bool:
        """Trigger redo for specific entities in a pipeline"""
        logger.info(
            f"Redo entities {entity_ids} in pipeline {pipeline_id} (with_snapshot={with_snapshot}, snapshot_write_method={snapshot_write_method})"
        )
        loop = asyncio.get_running_loop()
        success = True

        for entity_id in entity_ids:
            logger.info(f"Redoing entity {entity_id}")
            result = await loop.run_in_executor(
                None, self.client.redo_entity, pipeline_id, entity_id, with_snapshot, snapshot_write_method
            )
            if not result:
                logger.error(f"Failed to redo entity {entity_id}")
                success = False

            await asyncio.sleep(self.client.entity_start_timeout)

        return success
    
    async def pause_pipeline(self, pipeline_id: str) -> bool:
        """Stop all entities in a pipeline
        
        Args:
            pipeline_id: Pipeline ID
            
        Returns:
            bool: True if operation was successful, False otherwise
        """
        logger.info(f"Stopping pipeline {pipeline_id}")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.client.stop_pipeline, pipeline_id)
    
    async def pause_entities(self, pipeline_id: str, entity_ids: List[str]) -> bool:
        """Stop specific entities in a pipeline
        
        Args:
            pipeline_id: Pipeline ID
            entity_ids: List of entity IDs to stop
            
        Returns:
            bool: True if all operations were successful, False otherwise
        """
        logger.info(f"Stopping entities {entity_ids} in pipeline {pipeline_id}")
        loop = asyncio.get_running_loop()
        success = True
        
        for entity_id in entity_ids:
            logger.info(f"Stopping entity {entity_id}")
            result = await loop.run_in_executor(
                None, self.client.stop_entity, pipeline_id, entity_id
            )
            if not result:
                logger.error(f"Failed to stop entity {entity_id}")
                success = False
            
            # Wait a short time between entity operations to avoid overwhelming the Core Hub
            await asyncio.sleep(self.client.entity_start_timeout)
        
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
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.client.resync_pipeline, pipeline_id, snapshot_write_method
        )
    
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
        loop = asyncio.get_running_loop()
        success = True
        
        for entity_id in entity_ids:
            logger.info(f"Resyncing entity {entity_id}")
            result = await loop.run_in_executor(
                None, self.client.resync_entity, pipeline_id, entity_id, snapshot_write_method
            )
            if not result:
                logger.error(f"Failed to resync entity {entity_id}")
                success = False
            
            # Wait a short time between entity operations to avoid overwhelming the Core Hub
            await asyncio.sleep(self.client.entity_start_timeout)
        
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
    
    async def enter_maintenance_mode(self, pipeline_id: str) -> bool:
        """Enter maintenance mode for a pipeline
        
        Args:
            pipeline_id: Pipeline ID
            
        Returns:
            bool: True if operation was successful, False otherwise
        """
        logger.info(f"Entering maintenance mode for pipeline {pipeline_id}")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.client.enter_maintenance_mode, pipeline_id)
    
    async def exit_maintenance_mode(self, pipeline_id: str) -> bool:
        """Exit maintenance mode for a pipeline
        
        Args:
            pipeline_id: Pipeline ID
            
        Returns:
            bool: True if operation was successful, False otherwise
        """
        logger.info(f"Exiting maintenance mode for pipeline {pipeline_id}")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.client.exit_maintenance_mode, pipeline_id)


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
