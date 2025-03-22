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
This script sets up mock modules for testing and then runs the app.
It injects mock implementations of gluesync_sdk and gluesync_sdk_client
into sys.modules before importing the app, allowing tests to run without
the actual SDK installed.
"""

import sys
import os
import logging
import importlib.util
import traceback

# Configure logging
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("run_with_mock")

logger.info("Setting up mock modules for testing...")

# Add the tests directory to the Python path
tests_dir = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, tests_dir)
logger.info(f"Added tests directory to Python path: {tests_dir}")

# Add the project root to the Python path
project_root = os.path.abspath(os.path.join(tests_dir, '..'))
sys.path.insert(0, project_root)
logger.info(f"Added project root to Python path: {project_root}")

# Make sure these modules are available for import
required_modules = ['os', 'sys', 'logging', 'types', 'traceback']
for module_name in required_modules:
    if module_name not in sys.modules:
        logger.info(f"Importing required module: {module_name}")
        try:
            importlib.import_module(module_name)
        except ImportError as e:
            logger.error(f"Failed to import {module_name}: {e}")
            sys.exit(1)

# Import mock modules
try:
    logger.info("Setting up mock modules...")
    
    # Set the USE_MOCK environment variable
    os.environ["USE_MOCK"] = "true"
    
    # Import the mock modules directly
    logger.info("Importing mock_gluesync_sdk...")
    import mock_gluesync_sdk
    logger.info("Importing mock_gluesync_sdk_client...")
    import mock_gluesync_sdk_client
    logger.info("Importing mock_crontab...")
    import mock_crontab
    logger.info("Importing mock_croniter...")
    import mock_croniter
    
    # The mock_gluesync_sdk module will install itself in sys.modules['gluesync_sdk']
    # We just need to set up the gluesync_sdk_client module
    import types
    mock_gluesync_sdk_client_module = types.ModuleType('gluesync_sdk_client')
    mock_gluesync_sdk_client_module.gluesync_sdk_client = mock_gluesync_sdk_client.gluesync_sdk_client
    sys.modules['gluesync_sdk_client'] = mock_gluesync_sdk_client_module
    
    # Set up the mock crontab module
    mock_crontab_module = types.ModuleType('crontab')
    mock_crontab_module.CronTab = mock_crontab.CronTab
    # Make sure any existing imports of crontab are also patched
    sys.modules['crontab'] = mock_crontab_module
    
    # Set up the mock croniter module
    mock_croniter_module = types.ModuleType('croniter')
    mock_croniter_module.croniter = mock_croniter.croniter
    sys.modules['croniter'] = mock_croniter_module
    
    # Log the patched modules
    logger.info("Mock modules installed:")
    logger.info(f"  crontab.CronTab: {sys.modules['crontab'].CronTab}")
    logger.info(f"  croniter.croniter: {sys.modules['croniter'].croniter}")
    
    logger.info("Successfully installed mock modules")
except Exception as e:
    logger.error(f"Error setting up mock modules: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Now import and run the app
try:
    logger.info("Importing app module...")
    import app
    logger.info("App imported successfully")
    
    # Initialize the SDK client
    from gluesync_sdk_client import gluesync_sdk_client
    logger.info("Initializing mock SDK client...")
    gluesync_sdk_client.initialize()
    logger.info("Mock SDK client initialized successfully")
    
    # Initialize the database before starting the app
    logger.info("Initializing database...")
    from database import Base, engine
    logger.info(f"Creating database tables in {os.environ.get('DB_URL', 'default DB')}")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created successfully")
    
    # Import the app
    logger.info("App imported successfully, ready to be used by tests")
    from app import app as application
    
    # Get host and port from environment variables
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", 8000))
    
    logger.info(f"App will run on {host}:{port}")
    
    # Run the app
    import uvicorn
    if __name__ == "__main__":
        logger.info("Starting the app using uvicorn")
        uvicorn.run(application, host=host, port=port)
except Exception as e:
    logger.error(f"Error running app: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
