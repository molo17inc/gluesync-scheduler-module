# This file is part of Gluesync Scheduler Module (aka Chronos).
# Gluesync Scheduler Module (aka Chronos) is dual-licensed under the following licenses:

# 1. GNU General Public License (GPL) Version 3
#    You may use, modify, and distribute this software under the terms of the GPL v3.
#    See the LICENSE-GPL file or <http://www.gnu.org/licenses/gpl-3.0.html> for details.
#    This option is available at no cost, but any derivative works must also be licensed under GPL v3.
# 2. MOLO17 Commercial License
#    Alternatively, you may use this software under the MOLO17 Commercial License,
#    which includes a warranty and permits proprietary use. Contact MOLO17 at info@molo17.com
#    for licensing terms and conditions.

# You must choose one of these licenses to use this software. Using this software implies
# acceptance of one of these licenses. See the accompanying LICENSE files or contact
# MOLO17 for more information.

# Copyright (C) 2025 MOLO17. All rights reserved.

"""
Cross-platform path utilities for Windows and Unix compatibility
"""

import os
import platform
from pathlib import Path
from typing import Union, Optional
import logging

logger = logging.getLogger(__name__)

def get_platform_specific_path(unix_path: str, windows_path: str) -> str:
    """
    Get platform-specific path based on the current operating system
    
    Args:
        unix_path: Path for Unix-like systems (Linux, macOS)
        windows_path: Path for Windows systems
        
    Returns:
        Appropriate path for the current platform
    """
    if platform.system() == "Windows":
        return windows_path
    return unix_path

def normalize_path(path: str) -> str:
    """
    Normalize a path for the current platform
    
    Args:
        path: Path to normalize
        
    Returns:
        Normalized path using the correct separators
    """
    return os.path.normpath(path)

def ensure_directory_exists(path: Union[str, Path]) -> bool:
    """
    Ensure a directory exists, creating it if necessary
    
    Args:
        path: Directory path to create
        
    Returns:
        True if directory exists or was created successfully
    """
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        logger.error(f"Failed to create directory {path}: {e}")
        return False

def get_safe_sqlite_url(db_path: str) -> str:
    """
    Get a cross-platform SQLite URL
    
    Args:
        db_path: Database file path
        
    Returns:
        Properly formatted SQLite URL
    """
    # Convert to absolute path and normalize
    abs_path = os.path.abspath(db_path)
    
    # On Windows, we need to handle drive letters properly
    if platform.system() == "Windows":
        # Convert backslashes to forward slashes for SQLite URL
        abs_path = abs_path.replace("\\", "/")
        # Ensure we have the correct number of slashes after sqlite:
        return f"sqlite:///{abs_path}"
    else:
        return f"sqlite:///{abs_path}"

def get_log_directory() -> str:
    """
    Get the appropriate log directory for the current platform
    
    Returns:
        Platform-appropriate log directory path
    """
    if platform.system() == "Windows":
        # Check if we're in a Docker container
        if os.path.exists("C:\\app"):
            return "C:\\app\\logs"
        else:
            # Local Windows development
            return os.path.join(os.getcwd(), "logs")
    else:
        # Unix-like systems
        if os.path.exists("/app"):
            return "/app/logs"
        else:
            return os.path.join(os.getcwd(), "logs")

def get_data_directory() -> str:
    """
    Get the appropriate data directory for the current platform
    
    Returns:
        Platform-appropriate data directory path
    """
    if platform.system() == "Windows":
        # Check if we're in a Docker container
        if os.path.exists("C:\\app"):
            return "C:\\app\\data"
        else:
            # Local Windows development
            return os.path.join(os.getcwd(), "data")
    else:
        # Unix-like systems
        if os.path.exists("/app"):
            return "/app/data"
        else:
            return os.path.join(os.getcwd(), "data")

def get_gluesync_config_path() -> str:
    """
    Get the appropriate Gluesync configuration directory path
    
    Returns:
        Platform-appropriate Gluesync config directory path
    """
    if platform.system() == "Windows":
        return "C:\\opt\\gluesync\\data"
    else:
        return "/opt/gluesync/data"

def join_path(*args) -> str:
    """
    Join path components using the appropriate separator for the current platform
    
    Args:
        *args: Path components to join
        
    Returns:
        Joined path with correct separators
    """
    return os.path.join(*args)

def get_temp_directory() -> str:
    """
    Get the appropriate temporary directory for the current platform
    
    Returns:
        Platform-appropriate temporary directory path
    """
    if platform.system() == "Windows":
        return os.environ.get("TEMP", "C:\\temp")
    else:
        return os.environ.get("TMPDIR", "/tmp")

def is_windows() -> bool:
    """
    Check if the current platform is Windows
    
    Returns:
        True if running on Windows, False otherwise
    """
    return platform.system() == "Windows"

def get_project_root() -> str:
    """
    Get the project root directory in a cross-platform way
    
    Returns:
        Absolute path to the project root
    """
    # Get the directory containing this file
    current_dir = Path(__file__).parent
    
    # Navigate up to find the project root (where setup.py or main.py exists)
    while current_dir.parent != current_dir:  # Stop at filesystem root
        if (current_dir / "setup.py").exists() or (current_dir / "main.py").exists():
            return str(current_dir)
        current_dir = current_dir.parent
    
    # Fallback to current working directory
    return os.getcwd()
