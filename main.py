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

# This is the main entry point for the application
# It imports from the reorganized package structure

from gluesync_scheduler.core.app import app

if __name__ == "__main__":
    # Import and run the app from the reorganized package
    import uvicorn
    import os
    
    # Check if SSL is enabled
    ssl_enabled = os.getenv('SSL_ENABLED', 'False').lower() in ('true', '1', 't')
    if ssl_enabled:
        # Get SSL certificate paths from environment
        cert_file = os.getenv('SSL_CERT_FILE')
        key_file = os.getenv('SSL_KEY_FILE')
        
        if cert_file and key_file and os.path.exists(cert_file) and os.path.exists(key_file):
            # Run with SSL
            host = os.getenv('HOST', '0.0.0.0')
            port = int(os.getenv('PORT', '8000'))
            uvicorn.run(
                "gluesync_scheduler.core.app:app", 
                host=host, 
                port=port,
                ssl_keyfile=key_file,
                ssl_certfile=cert_file
            )
        else:
            # Fall back to HTTP if certificate files are missing
            print("Warning: SSL_ENABLED is True but certificate files not found. Falling back to HTTP.")
            host = os.getenv('HOST', '0.0.0.0')
            port = int(os.getenv('PORT', '8000'))
            uvicorn.run("gluesync_scheduler.core.app:app", host=host, port=port)
    else:
        # Run without SSL
        host = os.getenv('HOST', '0.0.0.0')
        port = int(os.getenv('PORT', '8000'))
        uvicorn.run("gluesync_scheduler.core.app:app", host=host, port=port)
