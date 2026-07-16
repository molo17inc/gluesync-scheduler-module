"""
User roles + permission helpers for Chronos.

Mirrors the CoreHub Kotlin ``UserRole`` enum
(``commons-model``) and the UI-side ``getUserRolePermissions.ts``
(gluesync-nodejs-monorepo MR !407).

Permission decisions:

- ``can_manage_schedules``  (create / edit / delete):  SUPER_ADMIN + MANAGER
- ``can_control_schedules`` (run-now, enable/disable): SUPER_ADMIN + MANAGER + MONITOR
- ``can_modify_configuration`` (timezone, chained events settings):
    SUPER_ADMIN + MANAGER. Matches the UI (MR !407); note that CoreHub's
    own ``Authorization.kt`` reserves ``canModifyConfiguration`` for
    SUPER_ADMIN only. See ``docs/auth-plan.md`` \u00a79 decision #1.
"""

from __future__ import annotations

from enum import Enum


class UserRole(str, Enum):
    """User roles recognised by CoreHub + Chronos.

    Value must match the string CoreHub emits in the JWT ``role`` claim /
    the ``/auth/me`` ``role`` field.
    """

    SUPER_ADMIN = "SUPER_ADMIN"
    MANAGER = "MANAGER"
    MONITOR = "MONITOR"
    VIEWER = "VIEWER"
    EXTERNAL_MODULE = "EXTERNAL_MODULE"

    @classmethod
    def parse(cls, value: str | None) -> "UserRole | None":
        """Parse a role string coming from CoreHub.

        Returns ``None`` for missing / unknown values so callers can
        translate to a 401/403 instead of crashing on new roles.
        """
        if not value:
            return None
        try:
            return cls(value)
        except ValueError:
            return None


# --- Permission helpers ------------------------------------------------


def can_manage_schedules(role: UserRole) -> bool:
    """True if ``role`` may create, edit, or delete schedules.

    Mirrors ``canManageSchedules`` in the UI permission model.
    """
    return role in {UserRole.SUPER_ADMIN, UserRole.MANAGER}


def can_control_schedules(role: UserRole) -> bool:
    """True if ``role`` may run-now or enable/disable existing schedules.

    Mirrors ``canControlSchedules`` in the UI permission model.
    """
    return role in {
        UserRole.SUPER_ADMIN,
        UserRole.MANAGER,
        UserRole.MONITOR,
    }


def can_modify_configuration(role: UserRole) -> bool:
    """True if ``role`` may edit chronos-global configuration (timezone, ...).

    Matches the UI's ``canModifyConfiguration`` flag: SUPER_ADMIN and
    MANAGER. Note that CoreHub's own ``Authorization.kt`` currently
    reserves this to SUPER_ADMIN only for its own config endpoints;
    the pre-existing UI-vs-CoreHub drift is documented in
    ``docs/auth-plan.md`` and out of scope for this MR.
    """
    return role in {UserRole.SUPER_ADMIN, UserRole.MANAGER}
