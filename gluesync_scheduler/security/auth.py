"""
FastAPI dependencies for end-user authorization.

Usage in a router:

.. code-block:: python

    from fastapi import Depends
    from gluesync_scheduler.security import (
        current_user, require_manage, require_control, require_config,
        CurrentUser,
    )

    @router.get("/jobs")
    async def list_jobs(user: CurrentUser = Depends(current_user)):
        ...  # any authenticated caller

    @router.post("/jobs")
    async def create_job(
        body: SchedulerJobDto,
        user: CurrentUser = Depends(require_manage),
    ):
        ...  # SUPER_ADMIN / MANAGER only

Any missing / invalid token yields **401**; any missing role permission
yields **403**. The introspection call to CoreHub is cached (default 30s)
so back-to-back UI clicks don't each pay a network round-trip.

See ``docs/auth-plan.md`` \u00a72.5 for the full request/response flow.
"""

from __future__ import annotations

import logging
import os

from fastapi import HTTPException, Request, status

from gluesync_scheduler.security.corehub_introspect import (
    CurrentUser,
    IntrospectionError,
    get_introspector,
)
from gluesync_scheduler.security.user_role import (
    can_control_schedules,
    can_manage_schedules,
    can_modify_configuration,
)

logger = logging.getLogger(__name__)


# --- Feature flags ----------------------------------------------------


def _fail_open_enabled() -> bool:
    """Debug escape hatch \u2014 must never be enabled in production.

    When true, ``current_user`` returns a synthetic SUPER_ADMIN identity
    regardless of the incoming credentials. Documented in
    ``docs/auth-plan.md`` \u00a78.
    """
    return os.getenv("CHRONOS_AUTH_FAIL_OPEN", "").lower() in (
        "1",
        "true",
        "yes",
    )


# --- Base dependency -------------------------------------------------


async def current_user(request: Request) -> CurrentUser:
    """Resolve the caller's identity via CoreHub introspection.

    Raises ``HTTPException(401)`` if the caller is not authenticated,
    or if CoreHub cannot be reached to verify the token.
    """
    if _fail_open_enabled():
        # Loud warning on every request so we notice if this ever gets
        # left on in a real deployment.
        from gluesync_scheduler.security.user_role import UserRole

        logger.warning(
            "CHRONOS_AUTH_FAIL_OPEN is enabled \u2014 bypassing authentication. "
            "This MUST NOT be set in production."
        )
        return CurrentUser(username="__fail_open__", role=UserRole.SUPER_ADMIN)

    cookie_header = request.headers.get("cookie")
    authorization_header = request.headers.get("authorization")
    introspector = get_introspector()

    try:
        user = await introspector.introspect(
            cookie_header=cookie_header,
            authorization_header=authorization_header,
        )
    except IntrospectionError as exc:
        # Transport-level failure. Fail closed with 401 and log details.
        logger.warning("Chronos auth introspection failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication verification failed",
        ) from exc

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return user


# --- Role gates -------------------------------------------------------


def _forbid(user: CurrentUser, action: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"Role {user.role.value} is not permitted to {action}"
        ),
    )


async def require_manage(request: Request) -> CurrentUser:
    """Guard for create / edit / delete operations on schedules.

    Allowed roles: SUPER_ADMIN, MANAGER.
    """
    user = await current_user(request)
    if not can_manage_schedules(user.role):
        raise _forbid(user, "manage schedules")
    return user


async def require_control(request: Request) -> CurrentUser:
    """Guard for run-now and enable/disable on existing schedules.

    Allowed roles: SUPER_ADMIN, MANAGER, MONITOR.
    """
    user = await current_user(request)
    if not can_control_schedules(user.role):
        raise _forbid(user, "control schedules")
    return user


async def require_config(request: Request) -> CurrentUser:
    """Guard for chronos-global configuration (timezone, chained-events settings).

    Allowed roles: SUPER_ADMIN, MANAGER. Matches the UI ``canModifyConfiguration``
    flag; note the pre-existing UI-vs-CoreHub drift documented in
    ``docs/auth-plan.md`` \u00a79.
    """
    user = await current_user(request)
    if not can_modify_configuration(user.role):
        raise _forbid(user, "modify configuration")
    return user
