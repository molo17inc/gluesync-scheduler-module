import os
from pathlib import Path
from typing import Tuple


def _normalize_path(path: Path) -> str:
    """Create a normalized representation of a path for duplicate detection."""
    if os.name == "nt":
        return os.path.normcase(str(path.resolve()))
    return str(path.resolve())


def resolve_gluesync_file(env_var: str, filename: str) -> Tuple[str, bool]:
    """Resolve the path for Gluesync configuration files with legacy fallbacks.

    Search order:
    1. Environment variable value (if provided and exists)
    2. /opt/gluesync/shared/{filename}
    3. Legacy directories:
       - Linux: /opt/gluesync/data/{filename}
       - Windows: C:\\opt\\gluesync\\data\\{filename} (directory created if missing)

    Returns the resolved path and a boolean indicating whether the file exists.
    The selected path is stored back into the environment variable for
    consistency across the process.
    """
    candidate_paths = []

    # 1. Explicit environment value (highest precedence if it exists)
    env_value = os.getenv(env_var)
    if env_value:
        candidate_paths.append(Path(env_value))

    # 2. Preferred shared directory
    candidate_paths.append(Path("/opt/gluesync/shared") / filename)

    # 3. Legacy directories per platform
    if os.name == "nt":
        legacy_dir = Path(r"C:\opt\gluesync\data")
    else:
        legacy_dir = Path("/opt/gluesync/data")
    candidate_paths.append(legacy_dir / filename)

    # Remove duplicates while preserving order
    unique_candidates = []
    seen = set()
    for path in candidate_paths:
        if not path:
            continue
        normalized = _normalize_path(path)
        if normalized not in seen:
            seen.add(normalized)
            unique_candidates.append(path)

    # Return first existing path
    for path in unique_candidates:
        if path.exists():
            os.environ[env_var] = str(path)
            return str(path), True

    # Ensure legacy directory exists (create on Windows as requested)
    try:
        legacy_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        # If we cannot create the directory (e.g., insufficient permissions), we still return the path
        pass

    fallback_path = unique_candidates[-1] if unique_candidates else (legacy_dir / filename)
    os.environ[env_var] = str(fallback_path)
    return str(fallback_path), False
