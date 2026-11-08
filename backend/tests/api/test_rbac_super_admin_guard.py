"""RBAC 高危账号操作保护（2026-11-09 审计）。

admin 不能重置/强制下线 super_admin（否则可借重置临时密码接管超管）。
"""
from __future__ import annotations

from backend.app.api.routes.rbac import _can_operate_super_admin


def test_super_admin_target_requires_super_admin_operator():
    assert _can_operate_super_admin("super_admin", "super_admin") is True
    assert _can_operate_super_admin("admin", "super_admin") is False
    assert _can_operate_super_admin("viewer", "super_admin") is False


def test_regular_targets_allow_admin_operator():
    assert _can_operate_super_admin("admin", "admin") is True
    assert _can_operate_super_admin("admin", "viewer") is True
    assert _can_operate_super_admin("admin", "") is True
