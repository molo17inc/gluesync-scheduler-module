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
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from api.router import router
from config import settings
from gluesync_sdk_client import gluesync_sdk_client

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Gluesync Scheduler Module",
    description="REST API service for scheduling tasks in Gluesync. This module provides endpoints to create, manage, and execute scheduled jobs for Gluesync pipelines and entities.",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    openapi_tags=[
        {
            "name": "jobs",
            "description": "Operations with scheduled jobs",
            "externalDocs": {
                "description": "Learn more about cron expressions",
                "url": "https://crontab.guru/",
            },
        },
    ],
    contact={
        "name": "MOLO17 Support",
        "url": "https://support.molo17.com",
        "email": "info@molo17.com",
    },
    license_info={
        "name": "Dual License: GNU GPL v3 / MOLO17 Commercial License",
        "url": "https://www.gnu.org/licenses/gpl-3.0.html",
    },
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup event handlers for Gluesync SDK client initialization and shutdown
@app.on_event("startup")
async def startup_event():
    logger.info("Initializing Gluesync SDK client...")
    try:
        # Set a timeout for the initialization to avoid hanging indefinitely
        # if the CoreHub is not available
        initialization_task = asyncio.create_task(gluesync_sdk_client.initialize())
        try:
            # Wait for the initialization to complete with a timeout
            await asyncio.wait_for(initialization_task, timeout=30.0)  # 30 seconds timeout
            logger.info("Gluesync SDK client initialized successfully")
            
            # Update the CoreHub URL from the SDK if available
            if gluesync_sdk_client.is_initialized and gluesync_sdk_client.corehub_url:
                sdk_corehub_url = gluesync_sdk_client.corehub_url
                logger.info(f"Using CoreHub URL from SDK: {sdk_corehub_url}")
                settings.update_corehub_url(sdk_corehub_url)
            else:
                logger.info(f"Using configured CoreHub URL: {settings.CORE_HUB_URL}")
        except asyncio.TimeoutError:
            logger.warning("Timeout while waiting for CoreHub discovery. The application will continue, "
                         "but CoreHub connection may not be available until a CoreHub is discovered.")
            # Don't cancel the task, let it continue in the background
            # so that it can connect when a CoreHub becomes available
    except Exception as e:
        logger.error(f"Failed to initialize Gluesync SDK client: {e}")
        logger.warning("Application will continue, but CoreHub connection may not be available")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down Gluesync SDK client...")
    try:
        await gluesync_sdk_client.shutdown()
        logger.info("Gluesync SDK client shutdown successfully")
    except Exception as e:
        logger.error(f"Error during Gluesync SDK client shutdown: {e}")

# Middleware to catch any uncaught exceptions
@app.middleware("http")
async def catch_exceptions_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as e:
        logger.exception(f"Uncaught exception: {e}")
        # Re-raise to let FastAPI handle the error response
        raise

# Include the API router
app.include_router(router, prefix="/api")

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
