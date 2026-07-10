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
from gluesync_scheduler.version import __version__
import pytz
from fastapi import FastAPI, Request
import fastapi
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response, JSONResponse
from fastapi.encoders import jsonable_encoder
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Any, Dict, List, Set

from gluesync_scheduler.api.router import router
from gluesync_scheduler.api.pipeline_router import router as pipeline_router
from gluesync_scheduler.api.settings_router import router as settings_router
from gluesync_scheduler.api.webhook_router import router as webhook_router
from gluesync_scheduler.core.gluesync_sdk_client import gluesync_sdk_client
from gluesync_scheduler.core.path_resolver import resolve_gluesync_file
from gluesync_scheduler.services.job_service import JobService
from gluesync_scheduler.services.scheduler_service import scheduler_service
from gluesync_scheduler.models.models import ScheduledJob
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

# Ensure Gluesync file paths resolve to supported locations
resolve_gluesync_file('GLUESYNC_LICENSE_FILE', 'gs-license.dat')
resolve_gluesync_file('GLUESYNC_SECURITY_CONFIG', 'security-config.json')

# Configure logging with rotation
logging.basicConfig(
    level=getattr(logging, os.getenv('LOG_LEVEL', 'INFO'), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            filename=os.path.join(os.getenv('LOG_DIR', './logs'), "chronos.log"),
            maxBytes=100 * 1024 * 1024,  # 100MB per file
            backupCount=9  # 9 backup files + current = 10 files total (1GB max)
        )
    ]
)
logger = logging.getLogger(__name__)

# Define a safe JSON encoder to prevent recursion errors
class SafeJSONEncoder:
    """Custom JSON encoder that prevents recursion errors.
    
    This encoder creates a simplified representation of complex objects and limits the depth of nested objects
    to prevent maximum recursion depth errors.
    """
    def __init__(self, max_depth=10):
        self.max_depth = max_depth
        self.current_depth = 0
        self.visited = set()  # Keep track of objects to detect cycles
    
    def encode(self, obj: Any) -> Any:
        """Safely encode an object to prevent recursion errors"""
        # Base case: Return primitives directly
        if obj is None or isinstance(obj, (str, int, float, bool)):
            return obj
        
        # Check for recursion depth limit
        if self.current_depth >= self.max_depth:
            return str(obj)[:100] + "..." if len(str(obj)) > 100 else str(obj)
        
        # Check for cycles
        obj_id = id(obj)
        if obj_id in self.visited:
            return f"<Circular reference to {type(obj).__name__} at {hex(obj_id)}>"
        
        self.visited.add(obj_id)
        self.current_depth += 1
        
        try:
            # Handle different types
            if isinstance(obj, dict):
                result = {}
                for k, v in list(obj.items())[:20]:  # Limit number of items
                    k_str = str(k) if not isinstance(k, (str, int, float, bool)) else k
                    result[k_str] = self.encode(v)
                if len(obj) > 20:
                    result["..."] = f"<{len(obj) - 20} more items>"
                return result
            
            elif isinstance(obj, (list, tuple, set)):
                result = []
                for item in list(obj)[:20]:  # Limit number of items
                    result.append(self.encode(item))
                if len(obj) > 20:
                    result.append(f"<{len(obj) - 20} more items>")
                return result
            
            # Handle other objects by converting them to a string representation
            return str(obj)[:100] + "..." if len(str(obj)) > 100 else str(obj)
        
        finally:
            # Clean up
            self.visited.remove(obj_id)
            self.current_depth -= 1

# Custom middleware to handle recursion errors in responses
class SafeJSONMiddleware(BaseHTTPMiddleware):
    """Middleware that prevents recursion errors in JSON responses"""
    
    async def dispatch(self, request: Request, call_next):
        try:
            # Process the request normally
            response = await call_next(request)
            return response
        except RecursionError as e:
            # If a recursion error occurs, return a simplified error response
            logger.error(f"Recursion error in response: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={
                    "error": "Internal server error",
                    "message": "Response too complex to process",
                    "detail": str(e),
                    "timestamp": datetime.now().isoformat()
                }
            )

# Create a custom JSON Response class that uses our safe encoder
class SafeJSONResponse(JSONResponse):
    """Custom JSONResponse that uses SafeJSONEncoder to prevent recursion errors"""
    
    def render(self, content: Any) -> bytes:
        try:
            # First try with the default JSON encoder
            return super().render(content)
        except RecursionError:
            # If that fails, use our safe encoder
            logger.warning("Using SafeJSONEncoder to handle complex response")
            encoder = SafeJSONEncoder()
            safe_content = encoder.encode(content)
            return super().render(safe_content)

# Create FastAPI app
app = FastAPI(
    title="Gluesync Scheduler Module",
    description="API for scheduling and managing pipelines in Gluesync",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    default_response_class=SafeJSONResponse  # Use our safe response class by default
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add the SafeJSONMiddleware to prevent recursion errors
app.add_middleware(SafeJSONMiddleware)

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

# Middleware to redirect HTTP to HTTPS when SSL_ENABLED is true
class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Only redirect if SSL is enabled and the request is not using HTTPS
        ssl_enabled = os.getenv('SSL_ENABLED', 'False').lower() in ('true', '1', 't')
        if ssl_enabled and request.url.scheme != "https":
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

# Add HTTPS redirect middleware if SSL is enabled
ssl_enabled = os.getenv('SSL_ENABLED', 'False').lower() in ('true', '1', 't')
if ssl_enabled:
    app.add_middleware(HTTPSRedirectMiddleware)

# Add exception handling middleware
app.middleware("http")(catch_exceptions_middleware)

@app.on_event("startup")
async def startup_event():
    """Initialize resources on startup"""
    # Display version at startup
    print(f"\n{'='*80}")
    print(f"Gluesync Chronos module v{__version__}")
    print(f"{'='*80}\n")
    
    logger.info("Starting Gluesync Chronos module...")
    
    # Create necessary directories
    os.makedirs(os.getenv('DATA_DIR', './data'), exist_ok=True)
    os.makedirs(os.getenv('LOG_DIR', './logs'), exist_ok=True)
    os.makedirs(os.getenv('CRON_LOG_DIR', './logs'), exist_ok=True)
    db_url = os.getenv('DB_URL')
    if db_url:
        # Use provided DB_URL
        pass
    else:
        # Convert relative path to absolute path for consistency
        abs_data_dir = os.path.abspath(os.getenv('DATA_DIR', './data'))
        db_url = f'sqlite:///{abs_data_dir}/scheduler.db'
        logger.info(f"Using absolute database path: {abs_data_dir}/scheduler.db")
    
    # Initialize database
    try:
        logger.info("Verifying database schema...")
        logger.info(f"DATABASE: Application DB_URL: {db_url}")
        
        # For SQLite, verify the database file
        if db_url.startswith('sqlite:'):
            db_path = db_url.replace('sqlite:///', '')
            # Handle Windows paths
            if ':' in db_path and len(db_path) > 2 and db_path[1] == ':':
                db_path = db_path[2:]  # Remove the leading / from /C:/path
            
            db_path = os.path.abspath(db_path)
            logger.info(f"DATABASE: Resolved database file path: {db_path}")
            
            # Check if database file exists
            if os.path.exists(db_path):
                db_size = os.path.getsize(db_path)
                logger.info(f"DATABASE: Database file exists, size: {db_size} bytes")
            else:
                logger.info("DATABASE: Database file does not exist, it will be created")
        
        # Run any pending migrations BEFORE creating tables
        logger.info("Checking for pending database migrations...")
        from gluesync_scheduler.db.migrate import run_migrations
        try:
            run_migrations()
            logger.info("Database migrations completed successfully")
        except Exception as mig_error:
            logger.warning(f"Migration check completed (may not be critical): {mig_error}")
        
        # Create all database tables if they don't exist
        from gluesync_scheduler.db.database import Base, engine
        logger.info("Creating database tables if they don't exist...")
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables verified/created successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        # Continue startup even if there's an error, as the schema might be partially functional
    
    # Initialize default settings
    try:
        from gluesync_scheduler.db.database import SessionLocal
        db = SessionLocal()
        
        # Check if settings table exists
        from sqlalchemy import inspect
        inspector = inspect(db.get_bind())
        
        logger.info(f"SSL_ENABLED before settings service: {os.getenv('SSL_ENABLED', 'False').lower() in ('true', '1', 't')}")
        
        if 'settings' in inspector.get_table_names():
            try:
                from gluesync_scheduler.services.settings_service import SettingsService
                settings_service = SettingsService(db)
                
                # Initialize default settings
                created_settings = settings_service.initialize_default_settings()
                if created_settings:
                    logger.info("Initialized default settings")
                else:
                    logger.info("Default settings already exist")
                
                # Load settings from database
                timezone_setting = settings_service.get_setting_by_key("timezone")
                if timezone_setting and timezone_setting.value:
                    logger.info(f"SSL_ENABLED before timezone update: {os.getenv('SSL_ENABLED', 'False').lower() in ('true', '1', 't')}")
                    timezone_value = timezone_setting.value
                    logger.info(f"Updated TIMEZONE to: {timezone_value}")
                    logger.info(f"SSL_ENABLED after timezone update: {os.getenv('SSL_ENABLED', 'False').lower() in ('true', '1', 't')}")
                    logger.info(f"Loaded timezone from database: {timezone_value}")
                    
            except Exception as e:
                logger.warning(f"Could not initialize settings (table may be empty): {e}")
        else:
            logger.warning("Settings table does not exist yet. Will be created when settings are first saved.")
            
        # Close the session
        db.close()
    except Exception as e:
        logger.error(f"Error initializing settings: {e}")
        logger.warning("The application will continue with default settings")
        db.close()
    
    # Initialize the Gluesync SDK client
    try:
        logger.info("Initializing Gluesync SDK client...")
        await gluesync_sdk_client.initialize()
        logger.info("Gluesync SDK client initialized successfully")
        
        # Wait a short time to ensure the SDK client has had time to extract the CoreHub URL
        await asyncio.sleep(0.5)
        
        # Extract the CoreHub URL from the SDK client
        corehub_url = None
        if hasattr(gluesync_sdk_client, 'corehub_url') and gluesync_sdk_client.corehub_url:
            corehub_url = gluesync_sdk_client.corehub_url
            logger.info(f"Extracted CoreHub URL from SDK client: {corehub_url}")
            
            # Update settings with the discovered URL
            # Note: Since we removed the settings system, we can't update it anymore
            logger.info(f"CoreHub URL from SDK client: {corehub_url}")
        
        # If we have a CoreHub URL in settings, log it
        gluesync_host = os.getenv('GLUESYNC_HOST', '')
        if gluesync_host:
            logger.info(f"CoreHub URL is set to: {gluesync_host}")
        else:
            logger.warning("CoreHub URL is not set yet. Will attempt to extract it from the SDK client.")
            
            # Try to extract it directly from the SDK client connection
            if hasattr(gluesync_sdk_client, '_client') and gluesync_sdk_client._client:
                if hasattr(gluesync_sdk_client._client, '_discovery_result') and gluesync_sdk_client._client._discovery_result:
                    host = gluesync_sdk_client._client._discovery_result.get('host')
                    port = gluesync_sdk_client._client._discovery_result.get('port', 1717)
                    use_ssl = gluesync_sdk_client._client._discovery_result.get('ssl', False)
                    
                    if host:
                        scheme = "https" if use_ssl else "http"
                        corehub_url = f"{scheme}://{host}:{port}"
                        logger.info(f"Extracted CoreHub URL from discovery result: {corehub_url}")
            
        # Create a CoreHubClient instance to initialize the singleton with the URL
        from gluesync_scheduler.core.play_pause import CoreHubClient
        client = CoreHubClient()
        logger.info("Initialized CoreHubClient singleton for later use")
    except Exception as e:
        logger.error(f"Failed to initialize Gluesync SDK client: {e}")
        logger.warning("The application will continue, but some functionality may be limited")
    
    # Load existing jobs into the scheduler
    try:
        logger.info("Loading existing jobs into scheduler...")
        
        # Import database components
        from gluesync_scheduler.db.database import Base, engine, SessionLocal
        from sqlalchemy import inspect
        
        # Create a database session
        db = SessionLocal()
        try:
            # Check if the scheduled_jobs table exists
            inspector = inspect(engine)
            if 'scheduled_jobs' in inspector.get_table_names():
                # Get all enabled jobs from the database
                enabled_jobs = db.query(ScheduledJob).filter(ScheduledJob.enabled == True).all()
                
                if enabled_jobs:
                    logger.info(f"Found {len(enabled_jobs)} enabled job(s) to load into scheduler")
                    
                    # Add each enabled job to the scheduler
                    for job in enabled_jobs:
                        try:
                            # Use the scheduler service to create the job
                            job_id = scheduler_service.create_job(job)
                            logger.info(f"Loaded job '{job.name}' (ID: {job.id}) into scheduler with scheduler ID: {job_id}")
                            
                            # Update the command field to store the scheduler job ID
                            job.command = f"Scheduled job ID: {job_id}"
                            db.commit()
                        except Exception as job_error:
                            logger.error(f"Failed to load job '{job.name}' (ID: {job.id}) into scheduler: {job_error}")
                            continue
                    
                    logger.info("Successfully loaded existing jobs into scheduler")
                else:
                    logger.info("No enabled jobs found to load into scheduler")
            else:
                logger.warning("scheduled_jobs table does not exist yet. No jobs to load.")
        finally:
            # Ensure the database session is closed
            db.close()
            
    except Exception as e:
        logger.error(f"Error loading jobs into scheduler: {e}")
        logger.warning("The scheduler will continue, but existing jobs may not be loaded")
    
    # Log configuration
    logger.info(f"Host: {os.getenv('HOST', '0.0.0.0')}")
    logger.info(f"Port: {int(os.getenv('PORT', '8000'))}")
    logger.info(f"Database URL: {db_url}")
    logger.info(f"SSL Enabled: {os.getenv('SSL_ENABLED', 'False').lower() in ('true', '1', 't')}")
    logger.info(f"CoreHub URL: {os.getenv('GLUESYNC_HOST', '')}")
    logger.info(f"Gluesync Module Tag: {os.getenv('GLUESYNC_MODULE_TAG', 'chronos')}")
    logger.info(f"Gluesync License File: {os.getenv('GLUESYNC_LICENSE_FILE', '/opt/gluesync/shared/gs-license.dat')}")
    logger.info(f"Gluesync Security Config: {os.getenv('GLUESYNC_SECURITY_CONFIG', '/opt/gluesync/shared/security-config.json')}")
    
    logger.info("Gluesync Scheduler Module started successfully")

    # Clean up any stale one-shot webhooks left in CoreHub from a previous run
    try:
        from gluesync_scheduler.services.chain_execution_service import ChainExecutionService
        removed = await ChainExecutionService.cleanup_stale_webhooks()
        if removed:
            logger.info("Startup cleanup removed %d stale webhook(s) from CoreHub", removed)
    except Exception as e:
        logger.warning("Stale webhook cleanup failed (non-fatal): %s", e)

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources on shutdown"""
    logger.info("Shutting down Gluesync Scheduler Module...")
    
    # Shutdown the Gluesync SDK client
    try:
        await gluesync_sdk_client.shutdown()
    except Exception as e:
        logger.error(f"Error shutting down Gluesync SDK client: {e}")
        
    # Shutdown the scheduler
    try:
        if hasattr(scheduler_service, 'scheduler') and scheduler_service.scheduler:
            logger.info("Shutting down scheduler...")
            scheduler_service.scheduler.shutdown()
            logger.info("Scheduler shut down successfully")
    except Exception as e:
        logger.error(f"Error shutting down scheduler: {e}")

# Middleware class for HTTPS redirection is defined above

# Include the API routers
app.include_router(router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(pipeline_router, prefix="/api")
app.include_router(webhook_router, prefix="/api")

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
    # Resolve security config file with fallbacks
    gluesync_security_config, config_exists = resolve_gluesync_file(
        'GLUESYNC_SECURITY_CONFIG', 'security-config.json'
    )
    if not config_exists:
        logger.warning(f"Security config file not found: {gluesync_security_config}")
        return None, None
    
    try:
        # Load the security config
        with open(gluesync_security_config, 'r') as f:
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
