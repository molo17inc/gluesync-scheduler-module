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
import sys
import json
import tempfile
import subprocess
import uvicorn
from shutil import which
from gluesync_scheduler.config.settings import settings

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
            print(f"Error reading security config: {e}")
    
    # If no PKCS12 file found or password missing, return None
    if not p12_path or not cert_password or not os.path.exists(p12_path):
        return None, None
        
    # Use certificate password for key password if not specified
    if not key_password:
        key_password = cert_password
        
    print(f"Using PKCS12 file: {p12_path} with password: {'*' * len(cert_password)} and key password: {'*' * len(key_password)}")
    
    # Extract PEM certificate and key from PKCS12 file
    try:
        # Check if openssl is available
        if which("openssl") is None:
            print("OpenSSL command not found. Make sure openssl is installed.")
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
            print(f"Certificate extraction output: {result.stdout}")
        except subprocess.CalledProcessError as e:
            print(f"Certificate extraction failed: {e.stderr}")
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
            print(f"Key extraction output: {result.stdout}")
        except subprocess.CalledProcessError as e:
            print(f"Key extraction failed: {e.stderr}")
            return None, None
            
        # Verify the extracted files exist and have content
        if not os.path.exists(temp_cert) or os.path.getsize(temp_cert) == 0:
            print(f"Certificate file missing or empty: {temp_cert}")
            return None, None
            
        if not os.path.exists(temp_key) or os.path.getsize(temp_key) == 0:
            print(f"Key file missing or empty: {temp_key}")
            return None, None
        
        print(f"Successfully extracted certificate and key from PKCS12 file: {p12_path}")
        return temp_cert, temp_key
    
    except Exception as e:
        print(f"Failed to extract certificate from PKCS12: {e}")
        return None, None

def main():
    """Run the Gluesync Scheduler Module"""
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
    
    if settings.SSL_ENABLED and cert_file and key_file and os.path.exists(cert_file) and os.path.exists(key_file):
        # Check if certificate files exist and are readable
        cert_valid = os.path.isfile(cert_file) and os.access(cert_file, os.R_OK)
        key_valid = os.path.isfile(key_file) and os.access(key_file, os.R_OK)
        
        if not cert_valid:
            print(f"Certificate file not accessible: {cert_file}")
            settings.SSL_ENABLED = False
            print("SSL not available - falling back to HTTP mode")
        elif not key_valid:
            print(f"Key file not accessible: {key_file}")
            settings.SSL_ENABLED = False
            print("SSL not available - falling back to HTTP mode")
        else:
            # Run with SSL
            print(f"Starting with SSL using cert: {cert_file} and key: {key_file}")
            try:
                uvicorn.run(
                    "gluesync_scheduler.core.app:app", 
                    host=settings.HOST, 
                    port=settings.PORT,
                    ssl_keyfile=key_file,
                    ssl_certfile=cert_file,
                    log_level="debug" if settings.DEBUG else "info"
                )
                # If we get here, it's because Uvicorn exited normally
                sys.exit(0)
            except Exception as e:
                print(f"Error starting HTTPS server: {e}")
                settings.SSL_ENABLED = False
                print("SSL not available - falling back to HTTP mode")
    else:
        # Fall back to HTTP if certificate files are missing
        if settings.SSL_ENABLED:
            print("Warning: SSL_ENABLED is True but certificate files not found. Falling back to HTTP.")
            # Set SSL_ENABLED to False in settings to ensure consistency
            settings.SSL_ENABLED = False
        
        print(f"Starting HTTP server at http://{settings.HOST}:{settings.PORT}")
        uvicorn.run(
            "gluesync_scheduler.core.app:app", 
            host=settings.HOST, 
            port=settings.PORT,
            log_level="debug" if settings.DEBUG else "info"
        )

if __name__ == "__main__":
    main()
