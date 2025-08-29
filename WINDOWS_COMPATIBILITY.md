# Windows Compatibility Guide

This document outlines the Windows path compatibility considerations and solutions implemented for the Gluesync Scheduler Module.

## Key Windows Path Compatibility Issues Identified

### 1. Hard-coded Unix Path Separators
**Issue**: The codebase uses Unix-style forward slashes (`/`) in several places that should use cross-platform path handling.

**Examples**:
- `/opt/gluesync/data/gs-license.dat` (should be `C:\opt\gluesync\data` on Windows)
- `./logs` and `./data` (should use appropriate Windows paths)
- SQLite URLs with Unix paths

### 2. Complex Path Construction
**Issue**: Multiple files use complex `os.path.join()` chains that are error-prone and platform-specific.

**Example**:
```python
# Problematic code in job_runner.py
log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs")
```

### 3. SQLite Database URL Path Handling
**Issue**: SQLite URLs with Unix-style paths may not work correctly on Windows.

**Example**:
```python
# Problematic
DB_URL = 'sqlite:///./data/scheduler.db'  # Unix-style relative path
```

### 4. Environment-Specific Path Defaults
**Issue**: Default paths assume Unix filesystem structure.

## Solutions Implemented

### 1. Cross-Platform Path Utilities (`path_utils.py`)
Created a comprehensive utility module with the following functions:

- `get_platform_specific_path()` - Returns appropriate path based on OS
- `get_safe_sqlite_url()` - Generates cross-platform SQLite URLs
- `get_log_directory()` - Returns platform-appropriate log directory
- `get_data_directory()` - Returns platform-appropriate data directory
- `get_gluesync_config_path()` - Returns Gluesync config directory path
- `normalize_path()` - Normalizes paths for current platform
- `ensure_directory_exists()` - Creates directories safely
- `is_windows()` - Platform detection utility

### 2. Updated Windows Dockerfile
**File**: `Dockerfile.windows`

**Key Features**:
- Uses Windows Server Core 2022 as base image
- Sets Windows-specific environment variables:
  ```dockerfile
  ENV DB_URL=sqlite:///C:/app/data/scheduler.db
  ENV LOG_DIR=C:\app\logs
  ENV CRON_LOG_DIR=C:\app\logs
  ENV DATA_DIR=C:\app\data
  ENV PYTHONPATH=C:\app
  ENV TEMP=C:\temp
  ENV TMP=C:\temp
  ```
- Creates proper Windows directory structure
- Uses PowerShell for health checks and operations

### 3. Updated Settings Configuration
**File**: `gluesync_scheduler/config/settings.py`

**Changes**:
- Imports cross-platform path utilities
- Uses `get_safe_sqlite_url()` for database URLs
- Uses `get_log_directory()` and `get_data_directory()` for default paths
- Uses `get_gluesync_config_path()` for Gluesync configuration paths

### 4. Updated Job Runner
**File**: `gluesync_scheduler/cli/job_runner.py`

**Changes**:
- Replaced complex path construction with `get_log_directory()`
- Uses cross-platform path utilities throughout

## Windows-Specific Environment Variables

The Windows Docker container sets these environment variables for proper operation:

| Variable                   | Windows Value                               | Purpose                                 |
| -------------------------- | ------------------------------------------- | --------------------------------------- |
| `DB_URL`                   | `sqlite:///C:/app/data/scheduler.db`        | Database file location                  |
| `LOG_DIR`                  | `C:\app\logs`                               | Application logs directory              |
| `CRON_LOG_DIR`             | `C:\app\logs`                               | Scheduled job logs directory            |
| `DATA_DIR`                 | `C:\app\data`                               | Application data directory              |
| `GLUESYNC_LICENSE_FILE`    | `C:\opt\gluesync\data\gs-license.dat`       | License file path                       |
| `GLUESYNC_SECURITY_CONFIG` | `C:\opt\gluesync\data\security-config.json` | Security config path                    |
| `PYTHONPATH`               | `C:\app`                                    | Python module search path               |
| `TEMP`                     | `C:\temp`                                   | Temporary files directory               |
| `TMP`                      | `C:\temp`                                   | Temporary files directory (alternative) |

## Directory Structure

### Windows Container Structure
```
C:\
├── app\
│   ├── data\           # Database and application data
│   ├── logs\           # Application and job logs
│   ├── gluesync_scheduler\  # Application code
│   ├── main.py
│   ├── run_scheduler.py
│   └── ...
├── opt\
│   └── gluesync\
│       └── data\       # Gluesync configuration files
│           ├── gs-license.dat
│           └── security-config.json
└── temp\               # Temporary files
```

### Unix Container Structure (for comparison)
```
/
├── app/
│   ├── data/           # Database and application data
│   ├── logs/           # Application and job logs
│   ├── gluesync_scheduler/  # Application code
│   ├── main.py
│   ├── run_scheduler.py
│   └── ...
├── opt/
│   └── gluesync/
│       └── data/       # Gluesync configuration files
│           ├── gs-license.dat
│           └── security-config.json
└── tmp/                # Temporary files
```

## Testing Windows Compatibility

### Local Windows Testing
1. Install Python 3.11 on Windows
2. Set environment variables:
   ```cmd
   set DB_URL=sqlite:///C:/path/to/your/data/scheduler.db
   set LOG_DIR=C:\path\to\your\logs
   set DATA_DIR=C:\path\to\your\data
   ```
3. Run the application: `python run_scheduler.py`

### Docker Windows Testing
1. Build Windows image: `docker build -f Dockerfile.windows -t scheduler-windows .`
2. Run container: `docker run -p 8000:8000 scheduler-windows`

## CI/CD Integration

The GitLab CI configuration includes:

1. **Windows Build Job** (`deploy-windows`):
   - Uses `windows-runner-hq` runners
   - Builds Windows Docker images with `-windows` suffix
   - Uses PowerShell scripts for Windows-specific operations

2. **Multi-Platform Manifest** (`deploy-manifest`):
   - Creates Docker manifests combining Linux and Windows images
   - Enables cross-platform deployment

## Migration Considerations

When migrating existing deployments to Windows:

1. **Database Migration**: SQLite databases are cross-platform compatible
2. **Log Files**: Existing log files can be copied to Windows paths
3. **Configuration**: Update environment variables to use Windows paths
4. **Volume Mounts**: Update Docker volume mounts to use Windows paths:
   ```yaml
   volumes:
     - "C:/host/data:/app/data"
     - "C:/host/logs:/app/logs"
   ```

## Troubleshooting

### Common Windows Path Issues

1. **Backslash Escaping**: Use raw strings or forward slashes in Python
   ```python
   # Good
   path = r"C:\app\data"
   path = "C:/app/data"
   
   # Avoid
   path = "C:\app\data"  # Escape sequences may cause issues
   ```

2. **Drive Letters**: Ensure SQLite URLs handle drive letters correctly
   ```python
   # Correct format
   sqlite:///C:/app/data/scheduler.db
   ```

3. **Case Sensitivity**: Windows is case-insensitive, but maintain consistent casing

### Debugging Path Issues

Enable debug logging to see resolved paths:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

The path utilities will log the resolved paths for debugging purposes.

## Future Considerations

1. **PowerShell Integration**: Consider using PowerShell for Windows-specific operations
2. **Windows Services**: Potential integration with Windows Service Manager
3. **Registry Configuration**: Option to store configuration in Windows Registry
4. **Windows Event Log**: Integration with Windows Event Log for system-level logging

## Conclusion

The implemented solutions provide comprehensive Windows compatibility while maintaining backward compatibility with Unix-based systems. The cross-platform path utilities ensure that the application works correctly on both Windows and Unix systems without code changes.
