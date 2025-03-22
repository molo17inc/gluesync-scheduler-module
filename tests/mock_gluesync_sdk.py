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
Mock implementation of the gluesync_sdk module for testing.
This allows tests to run without requiring the actual SDK to be installed.
"""

import sys
import logging

# Create a logger for the mock SDK
logger = logging.getLogger("mock_gluesync_sdk")

# Mock SDK classes and functions
class GluesyncSDK:
    """Mock implementation of the GluesyncSDK class"""
    
    def __init__(self):
        self.initialized = False
        self.connected = False
        self.token = "mock-token-123456"
        self.host = "localhost"
        self.port = 1717
        self.use_ssl = False
        
        # Storage for mock cron jobs
        self.cron_jobs = {}
        
        logger.info("Mock GluesyncSDK instance created")
        
    def _get_current_time(self):
        """Get the current time as an ISO 8601 string"""
        from datetime import datetime
        return datetime.now().isoformat()
    
    def initialize(self, **kwargs):
        """Mock initialization method"""
        self.initialized = True
        logger.info("Mock GluesyncSDK initialized with args: %s", kwargs)
        return True
    
    def connect(self, **kwargs):
        """Mock connect method"""
        self.connected = True
        logger.info("Mock GluesyncSDK connected with args: %s", kwargs)
        return True
    
    def disconnect(self):
        """Mock disconnect method"""
        self.connected = False
        logger.info("Mock GluesyncSDK disconnected")
        return True
    
    def get_token(self):
        """Mock get_token method"""
        logger.info("Mock GluesyncSDK returning token: %s", self.token)
        return self.token
        
    def create_cron_job(self, name, cron_expression, command=None, user=None, filename=None):
        """Mock create_cron_job method"""
        logger.info("Mock GluesyncSDK creating cron job: %s, %s", name, cron_expression)
        
        # For testing, we need to ensure we always return a valid job ID
        # We'll use a deterministic ID based on the name to ensure consistency
        import hashlib
        # Create a hash of the name and use the first 8 characters as the job ID
        job_id = hashlib.md5(name.encode()).hexdigest()[:8]
        
        # In the real SDK, either user or filename must be provided
        # For testing, we'll accept command without either and use defaults
        if not user and not filename:
            # Use a default user for testing
            user = "mock_user"
            logger.info(f"Using default user '{user}' for cron job")
        
        # Store the job in our mock database for later retrieval
        self.cron_jobs[job_id] = {
            "name": name,
            "cron_expression": cron_expression,
            "command": command,
            "user": user,
            "filename": filename,
            "created_at": self._get_current_time()
        }
        
        logger.info(f"Created cron job with ID: {job_id}")
        return job_id
        
    def update_cron_job(self, job_id, name=None, cron_expression=None, command=None, user=None, filename=None):
        """Mock update_cron_job method"""
        logger.info("Mock GluesyncSDK updating cron job: %s", job_id)
        
        # Check if the job exists
        if job_id not in self.cron_jobs:
            logger.warning(f"Cron job {job_id} not found")
            return False
            
        # Update the job properties
        if name is not None:
            self.cron_jobs[job_id]["name"] = name
        if cron_expression is not None:
            self.cron_jobs[job_id]["cron_expression"] = cron_expression
        if command is not None:
            self.cron_jobs[job_id]["command"] = command
        if user is not None:
            self.cron_jobs[job_id]["user"] = user
        if filename is not None:
            self.cron_jobs[job_id]["filename"] = filename
            
        # Update the timestamp
        self.cron_jobs[job_id]["updated_at"] = self._get_current_time()
        
        logger.info(f"Updated cron job {job_id}")
        return True
        
    def delete_cron_job(self, job_id):
        """Mock delete_cron_job method"""
        logger.info("Mock GluesyncSDK deleting cron job: %s", job_id)
        
        # Check if the job exists
        if job_id not in self.cron_jobs:
            logger.warning(f"Cron job {job_id} not found")
            return False
            
        # Remove the job
        del self.cron_jobs[job_id]
        logger.info(f"Deleted cron job {job_id}")
        return True
        
    def list_cron_jobs(self):
        """Mock list_cron_jobs method"""
        logger.info("Mock GluesyncSDK listing cron jobs")
        
        # Return a list of cron jobs with their IDs
        job_list = []
        for job_id, job_data in self.cron_jobs.items():
            job_info = job_data.copy()
            job_info["id"] = job_id
            job_list.append(job_info)
            
        logger.info(f"Found {len(job_list)} cron jobs")
        return job_list
        
    def get_cron_job(self, job_id):
        """Mock get_cron_job method"""
        logger.info(f"Mock GluesyncSDK getting cron job: {job_id}")
        
        # Check if the job exists
        if job_id not in self.cron_jobs:
            logger.warning(f"Cron job {job_id} not found")
            return None
            
        # Return the job with its ID
        job_info = self.cron_jobs[job_id].copy()
        job_info["id"] = job_id
        
        logger.info(f"Found cron job {job_id}")
        return job_info

class GluesyncClient:
    """Mock implementation of the GluesyncClient class"""
    
    def __init__(self, host=None, port=None, use_ssl=False, module_tag=None):
        self.host = host or "localhost"
        self.port = port or 8080
        self.use_ssl = use_ssl
        self.module_tag = module_tag or "scheduler-module"
        self.connected = False
        self.token = "mock-token-123456"
        logger.info("Mock GluesyncClient created with host=%s, port=%s, use_ssl=%s, module_tag=%s", 
                   self.host, self.port, self.use_ssl, self.module_tag)
    
    def connect(self, license_file=None, keystore_path=None, keystore_password=None, security_config=None):
        """Mock connect method"""
        self.connected = True
        logger.info("Mock GluesyncClient connected with license_file=%s", license_file)
        return self
    
    def disconnect(self):
        """Mock disconnect method"""
        self.connected = False
        logger.info("Mock GluesyncClient disconnected")
        return True
    
    def get_token(self):
        """Mock get_token method"""
        logger.info("Mock GluesyncClient returning token: %s", self.token)
        return self.token

# Mock exception classes
class GluesyncSDKException(Exception):
    """Base mock exception class for GluesyncSDK errors"""
    pass

class GluesyncError(GluesyncSDKException):
    """Mock exception class for general Gluesync errors"""
    pass

class GluesyncConnectionError(GluesyncError):
    """Mock exception class for Gluesync connection errors"""
    pass

class GluesyncAuthenticationError(GluesyncError):
    """Mock exception class for Gluesync authentication errors"""
    pass

class GluesyncLicenseError(GluesyncError):
    """Mock exception class for Gluesync license errors"""
    pass

# Create singleton instance
_instance = GluesyncSDK()

# Export the instance as if it were imported from the real SDK
sdk = _instance

# Install the mock module
# Don't rely on __name__ which might not be set correctly when imported with importlib
# Instead, create a new module and populate it directly
import types
gluesync_sdk_module = types.ModuleType('gluesync_sdk')

# Copy all attributes from this module to the mock module
for attr_name in dir():
    if not attr_name.startswith('__'):
        setattr(gluesync_sdk_module, attr_name, globals()[attr_name])

# Install the mock module
sys.modules['gluesync_sdk'] = gluesync_sdk_module
