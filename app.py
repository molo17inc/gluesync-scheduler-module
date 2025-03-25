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
        # Check if request is from a browser (not Postman or other API client)
        user_agent = request.headers.get("user-agent", "").lower()
        is_browser = "mozilla" in user_agent or "chrome" in user_agent or "safari" in user_agent or "edge" in user_agent
        
        # Only redirect browsers, not API clients like Postman
        if request.url.scheme == "http" and settings.SSL_ENABLED and is_browser:
            try:
                # Get the host from request headers
                host = request.headers.get("host", f"{settings.HOST}:{settings.PORT}")
                hostname = host.split(':')[0] if ':' in host else host
                
                # Create HTTPS URL (same port - we're not using dual mode)
                https_url = f"https://{hostname}:{settings.PORT}{request.url.path}"
                if request.url.query:
                    https_url += f"?{request.url.query}"
                
                logger.info(f"Redirecting browser from HTTP to HTTPS: {https_url}")
                return RedirectResponse(url=https_url, status_code=307)
            except Exception as e:
                logger.error(f"Error in redirect middleware: {e}")
        
        # Process normally for API clients and HTTPS requests
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

# Function to create SSL context for HTTPS server
def create_ssl_context():
    """Create SSL context with relaxed protocol settings for broader client compatibility"""
    if not settings.SSL_ENABLED:
        return None
    
    # Get certificate paths from environment variables or security config
    cert_file = os.getenv('SSL_CERT_FILE')
    key_file = os.getenv('SSL_KEY_FILE')
    
    # Check if certificate files exist
    if not (cert_file and key_file and os.path.exists(cert_file) and os.path.exists(key_file)):
        logger.warning("SSL certificate files not found or not specified. Running in HTTP mode.")
        settings.SSL_ENABLED = False
        return None
    
    try:
        # Create SSL context with TLSv1.0 minimum for broad compatibility
        logger.info(f"Setting up SSL context with certificate: {cert_file} and key: {key_file}")
        ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ssl_context.load_cert_chain(certfile=cert_file, keyfile=key_file)
        
        # Make SSL context more permissive to support older clients
        try:
            ssl_context.minimum_version = ssl.TLSVersion.TLSv1
            ssl_context.maximum_version = ssl.TLSVersion.TLSv1_3
            ssl_context.set_ciphers('DEFAULT')
        except (AttributeError, ValueError):
            # Fallback for older Python versions
            ssl_context.options &= ~ssl.OP_NO_TLSv1
            ssl_context.options &= ~ssl.OP_NO_TLSv1_1
        
        # Add warning for certificate verification
        if settings.SSL_SKIP_VERIFY:
            logger.warning("SSL certificate verification is DISABLED")
            logger.warning("For Chrome, type 'thisisunsafe' when certificate warning appears")
            logger.warning("For Firefox, you may need to add a security exception")
            
        return ssl_context
    except Exception as e:
        logger.error(f"Error creating SSL context: {e}")
        logger.warning("Falling back to HTTP mode")
        settings.SSL_ENABLED = False
        return None

# Extract certificates from PKCS12 file if available
def extract_from_pkcs12():
    """Extract certificate and key from PKCS12 file if available"""
    # Check for PKCS12 file path from environment or security config
    p12_path = os.getenv('SSL_P12_PATH')
    cert_password = os.getenv('SSL_CERT_PASSWORD')
    key_password = os.getenv('SSL_KEY_PASSWORD')
    
    # Check security config if environment variables not set
    if not p12_path and os.path.exists(settings.GLUESYNC_SECURITY_CONFIG):
        try:
            with open(settings.GLUESYNC_SECURITY_CONFIG, 'r') as config_file:
                security_config = json.load(config_file)
                if 'ssl' in security_config:
                    ssl_settings = security_config['ssl']
                    p12_path = ssl_settings.get('sslCertificatePath')
                    cert_password = ssl_settings.get('certificatePassword')
                    key_password = ssl_settings.get('certificateKeyPassword') or cert_password
        except Exception as e:
            logger.error(f"Error reading security config: {e}")
    
    # If no PKCS12 file found or password missing, return None
    if not p12_path or not cert_password or not os.path.exists(p12_path):
        return None, None
        
    # Use certificate password for key password if not specified
    if not key_password:
        key_password = cert_password
        
    logger.info(f"Using PKCS12 file: {p12_path} with password: {'*' * len(cert_password)} and key password: {'*' * len(key_password)}")
    
    # Extract PEM certificate and key from PKCS12 file
    try:
        import tempfile
        import subprocess
        from shutil import which
        
        # Check if openssl is available
        if which("openssl") is None:
            logger.error("OpenSSL command not found. Make sure openssl is installed.")
            return None, None
        
        # Create temporary directory for extraction
        temp_dir = tempfile.mkdtemp()
        temp_cert = os.path.join(temp_dir, "cert.pem")
        temp_key = os.path.join(temp_dir, "key.pem")
        
        # Extract certificate
        cert_cmd = [
            "openssl", "pkcs12", 
            "-in", p12_path, 
            "-passin", f"pass:{cert_password}",
            "-nokeys", "-out", temp_cert
        ]
        
        # Run with better error handling
        try:
            result = subprocess.run(cert_cmd, check=True, capture_output=True, text=True)
            logger.debug(f"Certificate extraction output: {result.stdout}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Certificate extraction failed: {e.stderr}")
            return None, None
        
        # Extract key without encryption (nodes = no DES encryption)
        key_cmd = [
            "openssl", "pkcs12", 
            "-in", p12_path, 
            "-passin", f"pass:{cert_password}",
            "-nocerts", "-out", temp_key,
            "-nodes"
        ]
        
        # Run with better error handling
        try:
            result = subprocess.run(key_cmd, check=True, capture_output=True, text=True)
            logger.debug(f"Key extraction output: {result.stdout}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Key extraction failed: {e.stderr}")
            return None, None
            
        # Verify the extracted files exist and have content
        if not os.path.exists(temp_cert) or os.path.getsize(temp_cert) == 0:
            logger.error(f"Certificate file missing or empty: {temp_cert}")
            return None, None
            
        if not os.path.exists(temp_key) or os.path.getsize(temp_key) == 0:
            logger.error(f"Key file missing or empty: {temp_key}")
            return None, None
        
        # Register cleanup function
        @app.on_event("shutdown")
        async def cleanup_temp_ssl_files():
            import shutil
            try:
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
                    logger.info(f"Removed temporary SSL files at {temp_dir}")
            except Exception as e:
                logger.error(f"Failed to remove temporary SSL files: {e}")
        
        logger.info(f"Successfully extracted certificate and key from PKCS12 file: {p12_path}")
        return temp_cert, temp_key
    
    except Exception as e:
        logger.error(f"Failed to extract certificate from PKCS12: {e}")
        return None, None

if __name__ == "__main__":
    # Check for SSL certificate paths in environment
    cert_file = os.getenv('SSL_CERT_FILE')
    key_file = os.getenv('SSL_KEY_FILE')
    
    # If not available, try extracting from PKCS12
    if not (cert_file and key_file and os.path.exists(cert_file) and os.path.exists(key_file)):
        cert_file, key_file = extract_from_pkcs12()
        if cert_file and key_file:
            # Set environment variables for the extracted files
            os.environ['SSL_CERT_FILE'] = cert_file
            os.environ['SSL_KEY_FILE'] = key_file
        else:
            # Fall back to HTTP if no certificates available
            settings.SSL_ENABLED = False
    
    if settings.SSL_ENABLED:
        # Create SSL context with better security settings but broader compatibility
        ssl_context = create_ssl_context()
        
        if ssl_context:
            logger.info(f"Starting HTTPS server at https://{settings.HOST}:{settings.PORT}")
            
            # Log helpful messages for handling certificate issues
            logger.info("If you're using Postman and getting protocol errors:")
            logger.info("1. Make sure to use https:// explicitly in the URL")
            logger.info("2. Disable SSL certificate verification in Postman settings")
            logger.info("3. If using a browser, you may need to accept security exceptions")
            
            # Check if certificate files exist and are readable
            cert_valid = os.path.isfile(cert_file) and os.access(cert_file, os.R_OK)
            key_valid = os.path.isfile(key_file) and os.access(key_file, os.R_OK)
            
            if not cert_valid:
                logger.error(f"Certificate file not accessible: {cert_file}")
                settings.SSL_ENABLED = False
                logger.warning("SSL not available - falling back to HTTP mode")
            elif not key_valid:
                logger.error(f"Key file not accessible: {key_file}")
                settings.SSL_ENABLED = False
                logger.warning("SSL not available - falling back to HTTP mode")
            else:
                # Start HTTPS server
                try:
                    uvicorn.run(
                        "app:app",
                        host=settings.HOST,
                        port=settings.PORT,
                        ssl_keyfile=key_file,
                        ssl_certfile=cert_file,
                        reload=settings.DEBUG,
                        log_level="debug" if settings.DEBUG else "info"
                    )
                    # If we get here, it's because Uvicorn exited normally
                    sys.exit(0)
                except Exception as e:
                    logger.error(f"Error starting HTTPS server: {e}")
                    settings.SSL_ENABLED = False
                    logger.warning("SSL not available - falling back to HTTP mode")
        else:
            # Fall back to HTTP
            settings.SSL_ENABLED = False
            logger.warning("SSL not available - falling back to HTTP mode")
    
    # HTTP mode
    if not settings.SSL_ENABLED:
        logger.info(f"Starting HTTP server at http://{settings.HOST}:{settings.PORT}")
        uvicorn.run(
            "app:app",
            host=settings.HOST,
            port=settings.PORT,
            reload=settings.DEBUG,
            log_level="debug" if settings.DEBUG else "info"
        )
