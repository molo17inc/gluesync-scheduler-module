"""Unit tests for ``gluesync_scheduler.security.user_role``."""

from __future__ import annotations

import pytest

from gluesync_scheduler.security.user_role import (
    UserRole,
    can_control_schedules,
    can_manage_schedules,
    can_modify_configuration,
)


# --- Parsing ----------------------------------------------------------


class TestUserRoleParse:
    def test_parse_known_roles(self) -> None:
        assert UserRole.parse("SUPER_ADMIN") == UserRole.SUPER_ADMIN
        assert UserRole.parse("MANAGER") == UserRole.MANAGER
        assert UserRole.parse("MONITOR") == UserRole.MONITOR
        assert UserRole.parse("VIEWER") == UserRole.VIEWER
        assert UserRole.parse("EXTERNAL_MODULE") == UserRole.EXTERNAL_MODULE

    def test_parse_none_returns_none(self) -> None:
        assert UserRole.parse(None) is None

    def test_parse_empty_string_returns_none(self) -> None:
        assert UserRole.parse("") is None

    def test_parse_unknown_role_returns_none(self) -> None:
        # New roles introduced by CoreHub tomorrow must not crash us.
        assert UserRole.parse("OVERLORD") is None
        assert UserRole.parse("super_admin") is None  # case-sensitive on purpose

    def test_role_is_string_valued(self) -> None:
        # Downstream code compares against JSON strings and stores in
        # logs; str-Enum makes that painless.
        assert UserRole.SUPER_ADMIN.value == "SUPER_ADMIN"
        assert str(UserRole.MANAGER.value) == "MANAGER"


# --- Permission matrix -----------------------------------------------


ALL_ROLES = list(UserRole)


class TestPermissionMatrix:
    """One-liner asserts per (role, permission). Keeps regressions loud."""

    @pytest.mark.parametrize(
        "role,expected",
        [
            (UserRole.SUPER_ADMIN, True),
            (UserRole.MANAGER, True),
            (UserRole.MONITOR, False),
            (UserRole.VIEWER, False),
            (UserRole.EXTERNAL_MODULE, True),
        ],
    )
    def test_can_manage_schedules(self, role: UserRole, expected: bool) -> None:
        assert can_manage_schedules(role) is expected

    @pytest.mark.parametrize(
        "role,expected",
        [
            (UserRole.SUPER_ADMIN, True),
            (UserRole.MANAGER, True),
            (UserRole.MONITOR, True),
            (UserRole.VIEWER, False),
            (UserRole.EXTERNAL_MODULE, True),
        ],
    )
    def test_can_control_schedules(self, role: UserRole, expected: bool) -> None:
        assert can_control_schedules(role) is expected

    @pytest.mark.parametrize(
        "role,expected",
        [
            (UserRole.SUPER_ADMIN, True),
            (UserRole.MANAGER, True),  # matches UI MR !407
            (UserRole.MONITOR, False),
            (UserRole.VIEWER, False),
            (UserRole.EXTERNAL_MODULE, False),
        ],
    )
    def test_can_modify_configuration(
        self, role: UserRole, expected: bool
    ) -> None:
        assert can_modify_configuration(role) is expected


class TestPermissionInvariants:
    """Structural invariants over the permission functions."""

    def test_manage_implies_control(self) -> None:
        # Anyone who can create a schedule must also be allowed to run
        # / toggle it. Otherwise there are impossible workflows.
        for role in ALL_ROLES:
            if can_manage_schedules(role):
                assert can_control_schedules(role), (
                    f"{role} can manage but not control \u2014 broken matrix"
                )

    def test_super_admin_has_all_permissions(self) -> None:
        assert can_manage_schedules(UserRole.SUPER_ADMIN)
        assert can_control_schedules(UserRole.SUPER_ADMIN)
        assert can_modify_configuration(UserRole.SUPER_ADMIN)

    def test_viewer_has_no_write_permissions(self) -> None:
        assert not can_manage_schedules(UserRole.VIEWER)
        assert not can_control_schedules(UserRole.VIEWER)
        assert not can_modify_configuration(UserRole.VIEWER)

    def test_external_module_can_manage_and_control_schedules(self) -> None:
        # EXTERNAL_MODULE is a service-account role used by trusted internal
        # modules (e.g. the Gluesync bootstrapper) to create schedules via
        # the authenticated API. It may manage and control schedules but
        # must not modify chronos-global configuration.
        assert can_manage_schedules(UserRole.EXTERNAL_MODULE)
        assert can_control_schedules(UserRole.EXTERNAL_MODULE)
        assert not can_modify_configuration(UserRole.EXTERNAL_MODULE)
