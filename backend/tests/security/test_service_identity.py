"""tests/security/test_service_identity.py — 定时工作流服务身份（2026-10-08 #10）

契约：
  - service_workflow 角色只有 sql.read、数据范围 all
  - bind_service_identity 绑定后，tool 通道 SQL 策略上下文通过守卫前置门
    （权限 + G-20 空身份两关——ECS inventory_alert failed(0 steps) 的连环拒）
  - 退出还原：不污染线程复用场景
  - 回归锁：无绑定时（脚本/MCP 直调）依旧 fail-closed 拒绝
"""
from __future__ import annotations

import pytest

from backend.core.request_context import (
    get_tool_department,
    get_tool_roles,
    get_tool_tenant_id,
    get_tool_user_id,
)
from backend.security.authorization import (
    ROLE_DATA_SCOPE,
    ROLE_PERMISSION_CODES,
    build_tool_authorization_context,
)
from backend.security.service_identity import (
    SERVICE_WORKFLOW_ROLE,
    bind_service_identity,
    service_user_id,
)


class TestServiceRoleRegistration:
    def test_role_has_only_sql_read(self):
        assert ROLE_PERMISSION_CODES[SERVICE_WORKFLOW_ROLE] == frozenset({"sql.read"})

    def test_role_data_scope_all(self):
        assert ROLE_DATA_SCOPE[SERVICE_WORKFLOW_ROLE] == "all"

    def test_authorization_context_grants_sql_read(self):
        authz = build_tool_authorization_context(
            user_id=service_user_id("inventory_alert"),
            department="system",
            tenant_id="default",
            roles=(SERVICE_WORKFLOW_ROLE,),
        )
        assert authz.has_permission("sql.read")
        assert authz.data_scope == "all"
        assert authz.principal.user_id == "svc:workflow:inventory_alert"


class TestBindServiceIdentity:
    def test_bind_and_restore(self):
        with bind_service_identity("inventory_alert"):
            assert get_tool_user_id() == "svc:workflow:inventory_alert"
            assert get_tool_tenant_id() == "default"
            assert get_tool_roles() == (SERVICE_WORKFLOW_ROLE,)
            assert get_tool_department() == "system"
        # 退出还原：不假设外层为空，但不能残留服务身份
        assert get_tool_user_id() == ""
        assert get_tool_roles() in (None, ())

    def test_restore_keeps_outer_identity(self):
        from backend.core.request_context import set_tool_user_id

        set_tool_user_id("human-user")
        try:
            with bind_service_identity("daily_report"):
                assert get_tool_user_id() == "svc:workflow:daily_report"
            assert get_tool_user_id() == "human-user"
        finally:
            set_tool_user_id("")


class TestGuardAcceptsServiceIdentity:
    """端到端守卫门：绑定后 tool 通道 SQL 策略上下文过 precheck 两连关。"""

    def _policy_context(self):
        from backend.tools.sql import tool_policy_context

        return tool_policy_context()

    def test_precheck_passes_when_bound(self):
        from backend.sql.policy import SQLPolicyGuard

        with bind_service_identity("inventory_alert"):
            policy = self._policy_context()
            # 不抛 = 权限门 + G-20 空身份门 + scope 合法性三关全过
            SQLPolicyGuard().precheck(policy)

    def test_unbound_still_denied(self):
        """回归锁：无绑定的旁路调用（脚本/MCP 直调）必须维持 fail-closed。"""
        from backend.sql.policy import SQLPolicyError, SQLPolicyGuard

        policy = self._policy_context()
        with pytest.raises(SQLPolicyError):
            SQLPolicyGuard().precheck(policy)
