# -*- coding: utf-8 -*-
"""sql.read 权限门测试（STOP B 决策 D3）。

矩阵锁定：viewer 无 sql.read → SQL_PERMISSION_DENIED；
editor/admin 有；映射唯一来源 security/authorization.ROLE_PERMISSION_CODES，
SQL 层不判角色。权限拒绝优先于一切 scope/表域判定。
"""
import pytest

from backend.sql.policy import (
    SQLPolicyGuard,
    SQLPolicyError,
    SQL_PERMISSION_DENIED,
    build_sql_policy_context,
)
from backend.sql.schema_loader import schema_loader

from tests.sql.conftest import build_ctx, make_ctx

EDITOR_ROLES = ("editor",)
ADMIN_ROLES = ("admin",)
VIEWER_ROLES = ("viewer",)


class TestPermissionMatrix:
    def test_viewer_denied(self):
        """viewer 无 sql.read → SQL_PERMISSION_DENIED。"""
        ctx = build_ctx(user_id="3", department="hr", roles=VIEWER_ROLES)
        assert not ctx.authz.has_permission("sql.read")
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(
                "SELECT sku FROM product.products", ctx)
        assert ei.value.code == SQL_PERMISSION_DENIED

    def test_editor_allowed(self):
        ctx = build_ctx(user_id="3", department="hr", roles=EDITOR_ROLES)
        assert ctx.authz.has_permission("sql.read")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            "SELECT sku FROM product.products", ctx)
        assert "product.products" in guarded.executable_sql

    def test_admin_allowed(self):
        ctx = build_ctx(user_id="9", roles=ADMIN_ROLES)
        assert ctx.authz.has_permission("sql.read")
        guarded = SQLPolicyGuard().validate_and_rewrite(
            "SELECT sku FROM product.products", ctx)
        assert guarded.executable_sql

    def test_unknown_role_denied(self):
        """未知角色推导不出 sql.read → 拒绝（fail-closed）。"""
        ctx = build_ctx(user_id="3", roles=("auditor",))
        assert not ctx.authz.has_permission("sql.read")
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(
                "SELECT sku FROM product.products", ctx)
        assert ei.value.code == SQL_PERMISSION_DENIED

    def test_guest_denied(self):
        """未认证主体（guest）无任何权限点 → 拒绝。"""
        ctx = build_ctx(user_id="", roles=())
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(
                "SELECT sku FROM product.products", ctx)
        assert ei.value.code == SQL_PERMISSION_DENIED


class TestPermissionGatePrecedence:
    def test_permission_denied_precedes_table_domain(self):
        """权限门先于表域判定：viewer 查 internal 表报 PERMISSION_DENIED
        而非 TABLE_NOT_ALLOWED（不向无权者泄露表域信息）。"""
        ctx = make_ctx("all", roles=VIEWER_ROLES, permission_codes=frozenset())
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(
                "SELECT amount FROM finance.expenses", ctx)
        assert ei.value.code == SQL_PERMISSION_DENIED

    def test_permission_denied_precedes_scope_check(self):
        """权限门先于 scope 合法性：无权限 + 非法 scope → PERMISSION_DENIED。"""
        ctx = make_ctx("nonsense", roles=VIEWER_ROLES,
                       permission_codes=frozenset())
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(
                "SELECT sku FROM product.products", ctx)
        assert ei.value.code == SQL_PERMISSION_DENIED


class TestSingleSourceMapping:
    def test_role_mapping_lives_only_in_authorization_module(self):
        """权限推导只来自 ROLE_PERMISSION_CODES（SQL 层不自判角色）：
        构造含 viewer+editor 双角色的主体，权限点 = 并集推导结果。"""
        ctx = build_ctx(user_id="3", roles=("viewer", "editor"))
        # editor 里有 sql.read → 双角色并集含 sql.read（取宽语义）
        assert ctx.authz.has_permission("sql.read")

    def test_scope_derived_from_roles_not_request(self):
        """data_scope 由 roles 推导（viewer=self），不经任何请求字段。"""
        ctx = build_ctx(user_id="3", roles=VIEWER_ROLES)
        assert ctx.data_scope == "self"
