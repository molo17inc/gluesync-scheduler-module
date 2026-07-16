"""
Security / authorization module for Chronos.

Delegates end-user JWT validation to CoreHub's ``GET /auth/me`` endpoint
and exposes FastAPI dependencies that gate routes on
``UserRole``-derived permissions.

See ``docs/auth-plan.md`` for the full design rationale.
"""

from gluesync_scheduler.security.user_role import (
    UserRole,
    can_control_schedules,
    can_manage_schedules,
    can_modify_configuration,
)
from gluesync_scheduler.security.exceptions import (
    ChronosSecurityError,
    IntrospectionError,
    PermissionDeniedError,
    UnauthenticatedError,
)
from gluesync_scheduler.security.corehub_introspect import (
    CoreHubIntrospector,
    CurrentUser,
    get_introspector,
)
from gluesync_scheduler.security.auth import (
    current_user,
    require_config,
    require_control,
    require_manage,
)

__all__ = [
    "UserRole",
    "can_manage_schedules",
    "can_control_schedules",
    "can_modify_configuration",
    "ChronosSecurityError",
    "UnauthenticatedError",
    "PermissionDeniedError",
    "IntrospectionError",
    "CoreHubIntrospector",
    "CurrentUser",
    "get_introspector",
    "current_user",
    "require_manage",
    "require_control",
    "require_config",
]
