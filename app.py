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
import json
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api.router import router
from api.pipeline_router import router as pipeline_router
from config import settings, Settings
from gluesync_sdk_client import gluesync_sdk_client

# Initialize settings to ensure data directory exists
settings = Settings()

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Gluesync Scheduler Module (aka Chronos)",
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

# Import database initialization functions
from database import Base, engine

# Setup event handlers for Gluesync SDK client initialization and shutdown
@app.on_event("startup")
async def startup_event():
    # Initialize database tables if they don't exist
    logger.info("Checking and initializing database tables...")
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database tables: {e}")
        logger.warning("Application may not function correctly without database tables")
    
    logger.info("Initializing Gluesync SDK client...")
    try:
        # Set a timeout for the initialization to avoid hanging indefinitely
        # if the CoreHub is not available
        initialization_task = asyncio.create_task(gluesync_sdk_client.initialize())
        try:
            # Wait for the initialization to complete with a timeout
            await asyncio.wait_for(initialization_task, timeout=30.0)  # 30 seconds timeout
            logger.info("Gluesync SDK client initialized successfully")
            
            # The CoreHub URL should already be updated in the SDK client's initialize method
            # if discovery was successful, but we'll check it here for completeness
            if gluesync_sdk_client.is_initialized:
                if gluesync_sdk_client.corehub_url:
                    logger.info(f"Using CoreHub URL from SDK: {gluesync_sdk_client.corehub_url}")
                elif settings.CORE_HUB_URL:
                    logger.info(f"Using configured CoreHub URL: {settings.CORE_HUB_URL}")
                else:
                    logger.warning("No CoreHub URL available. The application will continue, "
                                 "but CoreHub connection may not be available.")
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

# Middleware to redirect HTTP to HTTPS when SSL_ENABLED is true
class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Check if the request is HTTP and SSL is enabled
        if request.url.scheme == "http" and settings.SSL_ENABLED:
            # Get the host from request headers or use the default
            host = request.headers.get("host", f"{settings.HOST}:{settings.PORT}")
            if ':' in host:
                host_parts = host.split(':')
                # Keep the hostname but update the port for HTTPS
                host = f"{host_parts[0]}:{settings.PORT}"
            
            # Create the HTTPS URL
            https_url = f"https://{host}{request.url.path}"
            if request.url.query:
                https_url += f"?{request.url.query}"
            
            logger.info(f"Redirecting HTTP request to HTTPS: {https_url}")
            return RedirectResponse(url=https_url, status_code=307)
            
        return await call_next(request)

# Middleware to catch any uncaught exceptions
@app.middleware("http")
async def catch_exceptions_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as e:
        logger.exception(f"Uncaught exception: {e}")
        # Re-raise to let FastAPI handle the error response
        raise

# Include the API routers
app.include_router(router, prefix="/api")
app.include_router(pipeline_router, prefix="/api")

# Add HTTPS redirect middleware if SSL is enabled
if settings.SSL_ENABLED:
    app.add_middleware(HTTPSRedirectMiddleware)
    logger.info("HTTPS redirect middleware added")

if __name__ == "__main__":
    if settings.SSL_ENABLED:
        # Load security configuration for SSL
        ssl_config = {}
        
        # Check if security config file exists
        if os.path.exists(settings.GLUESYNC_SECURITY_CONFIG):
            logger.info(f"Using security config from: {settings.GLUESYNC_SECURITY_CONFIG}")
            
            try:
                # Load security config JSON file
                with open(settings.GLUESYNC_SECURITY_CONFIG, 'r') as config_file:
                    security_config = json.load(config_file)
                
                # Extract SSL configuration
                if 'ssl' in security_config:
                    ssl_settings = security_config['ssl']
                    # Note: Despite the variable name 'jks_path', this is actually a PKCS12 file
                    p12_path = ssl_settings.get('sslCertificatePath')
                    cert_password = ssl_settings.get('certificatePassword')
                    
                    logger.info(f"Found PKCS12 certificate path in security config: {p12_path}")
                    
                    # Fallback to environment variables if not specified in config
                    p12_path = os.getenv('SSL_P12_PATH', p12_path)
                    cert_password = os.getenv('SSL_CERT_PASSWORD', cert_password)
                    
                    # Use the variable name jks_path for compatibility with existing code
                    jks_path = p12_path
                    
                    # Check if certificate file exists (actually a PKCS12 file despite .jks extension)
                    if jks_path and os.path.exists(jks_path):
                        # The file is a PKCS12 certificate despite the .jks extension
                        # We'll need to extract the certificate and key for use with Uvicorn
                        
                        # First check if PEM certificate and key files are available from environment variables
                        cert_file = os.getenv('SSL_CERT_FILE')
                        key_file = os.getenv('SSL_KEY_FILE')
                        
                        if cert_file and key_file and os.path.exists(cert_file) and os.path.exists(key_file):
                            # Use PEM files if available from environment variables
                            ssl_config = {
                                "ssl_certfile": cert_file,
                                "ssl_keyfile": key_file
                            }
                            logger.info(f"SSL enabled with PEM certificate from env vars: {cert_file} and key: {key_file}")
                        else:
                            # Extract PEM certificate and key from PKCS12 file
                            try:
                                import tempfile
                                import subprocess
                                
                                # Create temporary directory for extraction
                                temp_dir = tempfile.mkdtemp()
                                temp_cert = os.path.join(temp_dir, "cert.pem")
                                temp_key = os.path.join(temp_dir, "key.pem")
                                
                                # Extract certificate from PKCS12
                                openssl_cert_cmd = [
                                    "openssl", "pkcs12", 
                                    "-in", jks_path, 
                                    "-passin", f"pass:{cert_password}",
                                    "-nokeys", "-out", temp_cert
                                ]
                                logger.info(f"Extracting certificate from PKCS12: {' '.join(openssl_cert_cmd)}")
                                subprocess.run(openssl_cert_cmd, check=True, capture_output=True)
                                
                                # Extract key from PKCS12
                                openssl_key_cmd = [
                                    "openssl", "pkcs12", 
                                    "-in", jks_path, 
                                    "-passin", f"pass:{cert_password}",
                                    "-nocerts", "-out", temp_key,
                                    "-passout", "pass:"
                                ]
                                logger.info(f"Extracting key from PKCS12: {' '.join(openssl_key_cmd)}")
                                subprocess.run(openssl_key_cmd, check=True, capture_output=True)
                                
                                # Use the extracted files for SSL configuration
                                ssl_config = {
                                    "ssl_certfile": temp_cert,
                                    "ssl_keyfile": temp_key
                                }
                                logger.info(f"Successfully extracted certificate and key from PKCS12. Using certificate: {temp_cert} and key: {temp_key}")
                                
                                # Register cleanup function to remove temp files on shutdown
                                @app.on_event("shutdown")
                                async def cleanup_temp_ssl_files():
                                    import shutil
                                    try:
                                        if os.path.exists(temp_dir):
                                            shutil.rmtree(temp_dir)
                                            logger.info(f"Removed temporary SSL extraction files at {temp_dir}")
                                    except Exception as e:
                                        logger.error(f"Failed to remove temporary SSL files: {e}")
                                        
                            except Exception as e:
                                logger.error(f"Failed to extract certificate and key from PKCS12: {e}")
                                logger.warning(f"Found PKCS12 file at {jks_path} but extraction failed")
                                logger.warning("Please manually extract PEM files or provide SSL_CERT_FILE and SSL_KEY_FILE")
                                logger.warning("Starting without SSL despite SSL_ENABLED=True")
                                settings.SSL_ENABLED = False
                    else:
                        logger.error(f"SSL is enabled but PKCS12 certificate file not found: {jks_path}")
                        
                        # Check for direct PEM files as fallback
                        cert_file = os.getenv('SSL_CERT_FILE')
                        key_file = os.getenv('SSL_KEY_FILE')
                        
                        if cert_file and key_file and os.path.exists(cert_file) and os.path.exists(key_file):
                            ssl_config = {
                                "ssl_certfile": cert_file,
                                "ssl_keyfile": key_file
                            }
                            logger.info(f"SSL enabled with fallback PEM certificate: {cert_file} and key: {key_file}")
                        else:
                            logger.warning("Starting without SSL despite SSL_ENABLED=True")
                            settings.SSL_ENABLED = False
                else:
                    logger.error("SSL section not found in security-config.json")
                    logger.warning("Starting without SSL despite SSL_ENABLED=True")
                    settings.SSL_ENABLED = False
            except Exception as e:
                logger.error(f"Error parsing security-config.json: {e}")
                logger.warning("Starting without SSL despite SSL_ENABLED=True")
                settings.SSL_ENABLED = False
        else:
            logger.error(f"SSL is enabled but security config file not found: {settings.GLUESYNC_SECURITY_CONFIG}")
            
            # Check for direct PEM files as fallback
            cert_file = os.getenv('SSL_CERT_FILE')
            key_file = os.getenv('SSL_KEY_FILE')
            
            if cert_file and key_file and os.path.exists(cert_file) and os.path.exists(key_file):
                ssl_config = {
                    "ssl_certfile": cert_file,
                    "ssl_keyfile": key_file
                }
                logger.info(f"SSL enabled with fallback PEM certificate: {cert_file} and key: {key_file}")
            else:
                logger.warning("Starting without SSL despite SSL_ENABLED=True")
                settings.SSL_ENABLED = False
        
        if settings.SSL_ENABLED:
            logger.info(f"Starting HTTPS server at https://{settings.HOST}:{settings.PORT}")
            uvicorn.run(
                "app:app",
                host=settings.HOST,
                port=settings.PORT,
                reload=settings.DEBUG,
                **ssl_config
            )
        else:
            logger.info(f"Starting HTTP server at http://{settings.HOST}:{settings.PORT}")
            uvicorn.run(
                "app:app",
                host=settings.HOST,
                port=settings.PORT,
                reload=settings.DEBUG,
            )
    else:
        logger.info(f"Starting HTTP server at http://{settings.HOST}:{settings.PORT}")
        uvicorn.run(
            "app:app",
            host=settings.HOST,
            port=settings.PORT,
            reload=settings.DEBUG,
        )
