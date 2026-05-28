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

from gluesync_scheduler.core.path_resolver import resolve_gluesync_file

# Extract certificates from PKCS12 file if available
def extract_from_pkcs12():
    """Extract certificate and key from PKCS12 file if available"""
    # Check for PKCS12 file path from environment or security config
    p12_path = os.getenv('SSL_P12_PATH')
    cert_password = os.getenv('SSL_CERT_PASSWORD')
    key_password = os.getenv('SSL_KEY_PASSWORD')
    
    # Check security config if environment variables not set
    gluesync_security_config, config_exists = resolve_gluesync_file(
        'GLUESYNC_SECURITY_CONFIG', 'security-config.json'
    )
    if not p12_path and config_exists:
        try:
            with open(gluesync_security_config, 'r') as config_file:
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
        # Check if openssl is available - try multiple methods
        openssl_path = None
        
        # Method 1: Use which() function
        if which("openssl"):
            openssl_path = which("openssl")
            print(f"Found OpenSSL via which(): {openssl_path}")
        
        # Method 2: Check common Windows paths
        if not openssl_path:
            common_paths = [
                r"C:\Program Files\OpenSSL\bin\openssl.exe",
                r"C:\Program Files (x86)\OpenSSL\bin\openssl.exe",
                r"C:\Chocolatey\bin\openssl.exe",
                r"C:\tools\openssl\bin\openssl.exe"
            ]
            for path in common_paths:
                if os.path.exists(path):
                    openssl_path = path
                    print(f"Found OpenSSL at: {openssl_path}")
                    break
        
        # Method 3: Check PATH environment variable
        if not openssl_path:
            import shutil
            openssl_path = shutil.which("openssl")
            if openssl_path:
                print(f"Found OpenSSL via shutil.which(): {openssl_path}")
        
        if not openssl_path:
            print("OpenSSL command not found in any expected location.")
            print("Attempting to use Python's built-in SSL libraries instead...")
            
            # Try using Python's cryptography library instead
            try:
                from cryptography.hazmat.primitives import serialization
                from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
                
                print("Using Python cryptography library for PKCS12 extraction...")
                
                # Read the PKCS12 file
                with open(p12_path, 'rb') as f:
                    pkcs12_data = f.read()
                
                # Load the key and certificates
                private_key, certificate, additional_certificates = load_key_and_certificates(
                    pkcs12_data, 
                    cert_password.encode() if cert_password else None
                )
                
                if not private_key or not certificate:
                    print("Failed to extract private key or certificate from PKCS12")
                    return None, None
                
                # Convert to PEM format
                cert_pem = certificate.public_bytes(serialization.Encoding.PEM).decode()
                key_pem = private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption()
                ).decode()
                
                # Create temporary directory for extraction
                temp_dir = tempfile.mkdtemp()
                temp_cert = os.path.join(temp_dir, "cert.pem")
                temp_key = os.path.join(temp_dir, "key.pem")
                
                # Write PEM files
                with open(temp_cert, 'w') as f:
                    f.write(cert_pem)
                with open(temp_key, 'w') as f:
                    f.write(key_pem)
                
                print(f"Successfully extracted certificate and key from PKCS12 file using Python cryptography: {p12_path}")
                return temp_cert, temp_key
                
            except ImportError:
                print("Python cryptography library not available for PKCS12 extraction.")
                return None, None
            except Exception as crypto_error:
                print(f"Failed to extract using Python cryptography: {crypto_error}")
                return None, None
        
        # Create temporary directory for extraction
        temp_dir = tempfile.mkdtemp()
        temp_cert = os.path.join(temp_dir, "cert.pem")
        temp_key = os.path.join(temp_dir, "key.pem")

        # Detect OpenSSL version to handle OpenSSL 3.x legacy algorithms
        try:
            version_result = subprocess.run([openssl_path, "version"], capture_output=True, text=True, check=True)
            openssl_version_str = version_result.stdout.strip()
            print(f"Detected OpenSSL version: {openssl_version_str}")
            is_openssl3 = "OpenSSL 3." in openssl_version_str
        except Exception as verr:
            print(f"Could not detect OpenSSL version: {verr}")
            is_openssl3 = False

        def try_extract(cert_extra_args=None, key_extra_args=None):
            cert_cmd = [
                openssl_path, "pkcs12",
                "-in", p12_path,
                "-passin", f"pass:{cert_password}",
                "-nokeys", "-out", temp_cert
            ]
            if cert_extra_args:
                cert_cmd.extend(cert_extra_args)

            key_cmd = [
                openssl_path, "pkcs12",
                "-in", p12_path,
                "-passin", f"pass:{cert_password}",
                "-nocerts", "-out", temp_key,
                "-nodes"
            ]
            if key_extra_args:
                key_cmd.extend(key_extra_args)

            try:
                cres = subprocess.run(cert_cmd, check=True, capture_output=True, text=True)
                print(f"Certificate extraction output: {cres.stdout}")
            except subprocess.CalledProcessError as ce:
                print(f"Certificate extraction failed: {ce.stderr}")
                return False

            try:
                kres = subprocess.run(key_cmd, check=True, capture_output=True, text=True)
                print(f"Key extraction output: {kres.stdout}")
            except subprocess.CalledProcessError as ke:
                print(f"Key extraction failed: {ke.stderr}")
                return False

            return True

        # First try standard extraction; if that fails on OpenSSL 3.x, retry with -legacy
        if not try_extract() and is_openssl3:
            print("Standard extraction failed, retrying with OpenSSL 3.x -legacy flag...")
            if not try_extract(cert_extra_args=["-legacy"], key_extra_args=["-legacy"]):
                print("Legacy extraction also failed.")
                return None, None

        # Verify the extracted files exist and have content
        if not os.path.exists(temp_cert) or os.path.getsize(temp_cert) == 0:
            print(f"Certificate file missing or empty: {temp_cert}")
            return None, None

        if not os.path.exists(temp_key) or os.path.getsize(temp_key) == 0:
            print(f"Key file missing or empty: {temp_key}")
            return None, None

        # Verify certificate chain completeness
        try:
            chain_check = subprocess.run(
                [openssl_path, "crl2pkcs7", "-nocrl", "-certfile", temp_cert],
                capture_output=True, text=True, check=True
            )
            cert_count = chain_check.stdout.count("-----BEGIN CERTIFICATE-----")
            print(f"Extracted certificate chain contains {cert_count} certificate(s)")
            if cert_count < 1:
                print("WARNING: No certificates found in extracted file")
            elif cert_count == 1:
                print("WARNING: Only leaf certificate found; intermediates may be missing. Browsers may show ERR_CERT_AUTHORITY_INVALID.")
        except Exception as chain_err:
            print(f"Could not verify certificate chain: {chain_err}")

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
            os.environ['SSL_ENABLED'] = 'False'
    
    ssl_enabled = os.getenv('SSL_ENABLED', 'False').lower() in ('true', '1', 't')
    if ssl_enabled and cert_file and key_file and os.path.exists(cert_file) and os.path.exists(key_file):
        # Check if certificate files exist and are readable
        cert_valid = os.path.isfile(cert_file) and os.access(cert_file, os.R_OK)
        key_valid = os.path.isfile(key_file) and os.access(key_file, os.R_OK)
        
        if not cert_valid:
            print(f"Certificate file not accessible: {cert_file}")
            os.environ['SSL_ENABLED'] = 'False'
            print("SSL not available - falling back to HTTP mode")
        elif not key_valid:
            print(f"Key file not accessible: {key_file}")
            os.environ['SSL_ENABLED'] = 'False'
            print("SSL not available - falling back to HTTP mode")
        else:
            # Run with SSL
            print(f"Starting with SSL using cert: {cert_file} and key: {key_file}")
            try:
                host = os.getenv('HOST', '0.0.0.0')
                port = int(os.getenv('PORT', '8000'))
                debug_enabled = os.getenv('DEBUG', 'False').lower() in ('true', '1', 't')
                uvicorn.run(
                    "gluesync_scheduler.core.app:app", 
                    host=host, 
                    port=port,
                    ssl_keyfile=key_file,
                    ssl_certfile=cert_file,
                    log_level="debug" if debug_enabled else "info"
                )
                # If we get here, it's because Uvicorn exited normally
                sys.exit(0)
            except Exception as e:
                print(f"Error starting HTTPS server: {e}")
                os.environ['SSL_ENABLED'] = 'False'
                print("SSL not available - falling back to HTTP mode")
    else:
        # Fall back to HTTP if certificate files are missing
        if ssl_enabled:
            print("Warning: SSL_ENABLED is True but certificate files not found. Falling back to HTTP.")
            # Set SSL_ENABLED to False in settings to ensure consistency
            os.environ['SSL_ENABLED'] = 'False'
        
        host = os.getenv('HOST', '0.0.0.0')
        port = int(os.getenv('PORT', '8000'))
        debug_enabled = os.getenv('DEBUG', 'False').lower() in ('true', '1', 't')
        print(f"Starting HTTP server at http://{host}:{port}")
        uvicorn.run(
            "gluesync_scheduler.core.app:app", 
            host=host, 
            port=port,
            log_level="debug" if debug_enabled else "info"
        )

if __name__ == "__main__":
    main()
