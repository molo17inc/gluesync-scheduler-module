"""
Version information for Gluesync Chronos module.

This module provides version information that is automatically generated during the CI/CD build process.
The version is read from a VERSION file in the project root at runtime.
"""
import os
from pathlib import Path
from typing import Optional

# Default version - will be overridden by CI
__version__ = "0.0.0-dev"

def get_version() -> str:
    """Get the current version of the application.
    
    Returns:
        str: The current version string
    """
    global __version__
    return __version__

def _read_version_file() -> Optional[str]:
    """Read version from VERSION file if it exists.
    
    Returns:
        Optional[str]: Version string if file exists and could be read, None otherwise
    """
    try:
        # Look for VERSION file in project root
        version_file = Path(__file__).parent.parent / "VERSION"
        if version_file.exists():
            version = version_file.read_text().strip()
            if version:
                return version
    except Exception as e:
        print(f"Warning: Could not read version file: {e}")
    return None

# Try to read version from file at module import
file_version = _read_version_file()
if file_version:
    __version__ = file_version
