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
from datetime import datetime
import pytz
from fastapi import FastAPI, Request
import fastapi
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from fastapi.encoders import jsonable_encoder
from starlette.middleware.base import BaseHTTPMiddleware

from gluesync_scheduler.api.router import router
from gluesync_scheduler.api.pipeline_router import router as pipeline_router
from gluesync_scheduler.config.settings import settings
from gluesync_scheduler.core.gluesync_sdk_client import gluesync_sdk_client

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(settings.LOG_DIR, "app.log"))
    ]
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="Gluesync Scheduler Module",
    description="API for scheduling and managing Gluesync pipeline operations",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Middleware to catch any uncaught exceptions
async def catch_exceptions_middleware(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as e:
        logger.error(f"Uncaught exception: {str(e)}")
        return Response(
            content=json.dumps({"detail": f"Internal server error: {str(e)}"}),
            status_code=500,
            media_type="application/json"
        )

# Add HTTPS redirect middleware if SSL is enabled
if settings.SSL_ENABLED:
    app.add_middleware(HTTPSRedirectMiddleware)

# Add exception handling middleware
app.middleware("http")(catch_exceptions_middleware)

@app.on_event("startup")
async def startup_event():
    """Initialize resources on startup"""
    logger.info("Starting Gluesync Scheduler Module...")
    
    # Create necessary directories
    os.makedirs(settings.LOG_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(settings.DB_URL.replace('sqlite:///', '')), exist_ok=True)
    
    # Initialize the Gluesync SDK client
    try:
        logger.info("Initializing Gluesync SDK client...")
        await gluesync_sdk_client.initialize()
        logger.info("Gluesync SDK client initialized successfully")
        
        # Update CoreHub URL from SDK if discovered
        if gluesync_sdk_client.corehub_url:
            settings.update_corehub_url(gluesync_sdk_client.corehub_url)
            logger.info(f"Updated CoreHub URL from SDK: {settings.CORE_HUB_URL}")
        elif hasattr(gluesync_sdk_client, '_client') and gluesync_sdk_client._client:
            # Try to extract CoreHub URL from the client's connection
            if hasattr(gluesync_sdk_client._client, '_host') and gluesync_sdk_client._client._host:
                host = gluesync_sdk_client._client._host
                port = getattr(gluesync_sdk_client._client, '_port', 1717)
                use_ssl = getattr(gluesync_sdk_client._client, '_use_ssl', False)
                protocol = 'https' if use_ssl else 'http'
                corehub_url = f"{protocol}://{host}:{port}"
                settings.update_corehub_url(corehub_url)
                logger.info(f"Extracted CoreHub URL from client connection: {settings.CORE_HUB_URL}")
    except Exception as e:
        logger.error(f"Failed to initialize Gluesync SDK client: {e}")
        logger.warning("The application will continue, but some functionality may be limited")
    
    # Log configuration
    logger.info(f"Host: {settings.HOST}")
    logger.info(f"Port: {settings.PORT}")
    logger.info(f"Database URL: {settings.DB_URL}")
    logger.info(f"SSL Enabled: {settings.SSL_ENABLED}")
    logger.info(f"CoreHub URL: {settings.CORE_HUB_URL}")
    logger.info(f"Gluesync Module Tag: {settings.GLUESYNC_MODULE_TAG}")
    logger.info(f"Gluesync License File: {settings.GLUESYNC_LICENSE_FILE}")
    logger.info(f"Gluesync Security Config: {settings.GLUESYNC_SECURITY_CONFIG}")
    
    logger.info("Gluesync Scheduler Module started successfully")

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on shutdown"""
    logger.info("Shutting down Gluesync Scheduler Module...")
    
    # Shutdown the Gluesync SDK client
    try:
        await gluesync_sdk_client.shutdown()
    except Exception as e:
        logger.error(f"Error shutting down Gluesync SDK client: {e}")

# Middleware to redirect HTTP to HTTPS when SSL_ENABLED is true
class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Only redirect if SSL is enabled and the request is not using HTTPS
        if settings.SSL_ENABLED and request.url.scheme != "https":
            # Get the host from the request
            host = request.headers.get("host", "")
            
            # If the host includes a port, remove it
            if ":" in host:
                host = host.split(":")[0]
            
            # Determine the HTTPS port (default is 443)
            https_port = 443
            
            # If we're using a non-standard HTTPS port, include it in the redirect URL
            port_str = f":{https_port}" if https_port != 443 else ""
            
            # Construct the redirect URL
            redirect_url = f"https://{host}{port_str}{request.url.path}"
            if request.url.query:
                redirect_url += f"?{request.url.query}"
            
            # Return a temporary redirect response
            return RedirectResponse(url=redirect_url, status_code=307)
        
        # If not redirecting, continue with the request
        return await call_next(request)

# Middleware class for HTTPS redirection is defined above

# Include the API routers
app.include_router(router, prefix="/api")
app.include_router(pipeline_router, prefix="/api")

# Custom JSON encoder to handle datetime objects
class JSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            # Ensure datetime has timezone information
            if obj.tzinfo is None:
                obj = obj.replace(tzinfo=pytz.UTC)
            return obj.isoformat()
        return super().default(obj)

# Override FastAPI's default JSON encoder
fastapi.encoders.jsonable_encoder = lambda obj, *args, **kwargs: json.loads(
    json.dumps(fastapi.encoders.jsonable_encoder(obj, *args, **kwargs), cls=JSONEncoder)
)

# Root endpoint that redirects to API documentation
@app.get("/")
async def root():
    return RedirectResponse(url="/api/docs")

def create_ssl_context():
    """Create SSL context with relaxed protocol settings for broader client compatibility"""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=os.getenv('SSL_CERT_FILE'), keyfile=os.getenv('SSL_KEY_FILE'))
    
    # Set the minimum TLS version to TLS 1.0 for broader compatibility
    # This is less secure but may be necessary for some clients
    context.minimum_version = ssl.TLSVersion.TLSv1
    
    # Enable all available cipher suites for maximum compatibility
    # This is less secure but may be necessary for some clients
    context.set_ciphers('ALL')
    
    return context

def extract_from_pkcs12():
    """Extract certificate and key from PKCS12 file if available"""
    # Check if we have a security config file
    if not os.path.exists(settings.GLUESYNC_SECURITY_CONFIG):
        logger.warning(f"Security config file not found: {settings.GLUESYNC_SECURITY_CONFIG}")
        return None, None
    
    try:
        # Load the security config
        with open(settings.GLUESYNC_SECURITY_CONFIG, 'r') as f:
            security_config = json.load(f)
        
        # Check if SSL is configured
        if 'ssl' not in security_config or 'sslCertificatePath' not in security_config['ssl']:
            logger.warning("SSL not configured in security config")
            return None, None
        
        # Get the certificate path and password
        cert_path = security_config['ssl']['sslCertificatePath']
        cert_password = security_config['ssl'].get('certificatePassword', '')
        
        if not os.path.exists(cert_path):
            logger.warning(f"Certificate file not found: {cert_path}")
            return None, None
        
        # Check if the certificate is a PKCS12 file (usually .p12 or .pfx, but may be .jks in Gluesync)
        if cert_path.endswith('.p12') or cert_path.endswith('.pfx') or cert_path.endswith('.jks'):
            logger.info(f"Extracting certificate and key from PKCS12 file: {cert_path}")
            
            # Create temporary files for the extracted certificate and key
            cert_file = os.path.join(os.path.dirname(cert_path), 'temp_cert.pem')
            key_file = os.path.join(os.path.dirname(cert_path), 'temp_key.pem')
            
            # Extract the certificate and key using OpenSSL
            # For .jks files, we need to convert to PKCS12 first, but in this case we assume
            # the .jks file is actually a PKCS12 file with a .jks extension (as is common in Gluesync)
            
            # Extract the certificate
            os.system(f'openssl pkcs12 -in "{cert_path}" -out "{cert_file}" -nokeys -passin pass:"{cert_password}"')
            
            # Extract the key
            os.system(f'openssl pkcs12 -in "{cert_path}" -out "{key_file}" -nocerts -nodes -passin pass:"{cert_password}"')
            
            # Check if the extraction was successful
            if os.path.exists(cert_file) and os.path.exists(key_file):
                logger.info(f"Certificate and key extracted successfully")
                
                # Register cleanup function to remove temporary files on shutdown
                @app.on_event("shutdown")
                async def cleanup_temp_files():
                    try:
                        if os.path.exists(cert_file):
                            os.remove(cert_file)
                        if os.path.exists(key_file):
                            os.remove(key_file)
                        logger.info("Temporary certificate and key files removed")
                    except Exception as e:
                        logger.error(f"Error removing temporary files: {e}")
                
                return cert_file, key_file
            else:
                logger.error("Failed to extract certificate and key from PKCS12 file")
                return None, None
        else:
            logger.info(f"Certificate file is not a PKCS12 file: {cert_path}")
            return None, None
    except Exception as e:
        logger.error(f"Error extracting certificate and key from PKCS12 file: {e}")
        return None, None
