# -*- coding: utf-8 -*-
"""fail-closed 汇总测试：一切无法确定性判定的情况必须拒绝，且拒绝为终态
（不进入带反馈重试——安全拒绝不允许模型绕过）。"""
import pytest

from backend.sql import policy as policy_mod
from backend.sql.policy import (
    SQLPolicyGuard,
    SQLPolicyError,
    SQL_PERMISSION_DENIED,
    SQL_SCOPE_UNAVAILABLE,
    SQL_TABLE_NOT_ALLOWED,
    build_sql_policy_context,
)
from backend.sql.sql_agent import get_sql_agent

from tests.sql.conftest import FIXTURE_TENANT_TABLE, make_ctx


class TestUnknownScope:
    @pytest.mark.parametrize("scope", ["foo", "", "ALL", "All", "tenant"])
    def test_unknown_scope_denied(self, scope):
        """未知/空/大小写变体 scope → 一律拒绝（unknown ≠ all，§三十八）。"""
        ctx = make_ctx(scope, department="hr")
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(
                "SELECT sku FROM product.products", ctx)
        assert ei.value.code == SQL_SCOPE_UNAVAILABLE

    def test_none_scope_denied(self):
        """data_scope=None（无角色主体）→ 拒绝。"""
        ctx = make_ctx(None)
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(
                "SELECT sku FROM product.products", ctx)
        assert ei.value.code == SQL_SCOPE_UNAVAILABLE


class TestFailClosedMatrix:
    def test_self_on_shared_denied(self):
        ctx = make_ctx("self", user_id="3")
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(
                "SELECT id FROM product.products", ctx)
        assert ei.value.code == SQL_TABLE_NOT_ALLOWED

    def test_missing_tenant_denied(self, fixture_tables):
        ctx = make_ctx("all", tenant_id="")
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(
                f"SELECT id FROM {FIXTURE_TENANT_TABLE}", ctx)
        assert ei.value.code == SQL_SCOPE_UNAVAILABLE

    def test_missing_department_denied(self, fixture_tables):
        ctx = make_ctx("department", department="")
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(
                "SELECT id FROM demo.dept_projects", ctx)
        assert ei.value.code == SQL_SCOPE_UNAVAILABLE

    def test_unmappable_self_denied(self):
        ctx = make_ctx("self", user_id="admin-2")
        with pytest.raises(SQLPolicyError) as ei:
            SQLPolicyGuard().validate_and_rewrite(
                'SELECT order_no FROM "order".orders', ctx)
        assert ei.value.code == SQL_SCOPE_UNAVAILABLE


class TestUserTextSafety:
    @pytest.mark.parametrize("code", [
        SQL_PERMISSION_DENIED,
        SQL_TABLE_NOT_ALLOWED,
        SQL_SCOPE_UNAVAILABLE,
    ])
    def test_user_text_is_safe_fixed_phrase(self, code):
        """对外文案为固定安全话术：不含表名/参数/内部细节。"""
        err = SQLPolicyError(code, f"内部详情: finance.expenses tenant-a hr")
        text = err.user_text
        assert "finance" not in text
        assert "tenant-a" not in text
        assert "hr" != text
        assert len(text) < 60


class TestSecurityRejectionIsTerminal:
    def _patch_agent_chain(self, monkeypatch):
        """隔离外部边界：选表与生成不调真实 LLM（只 mock 外部依赖）。"""
        monkeypatch.setattr("backend.sql.sql_agent.select_tables",
                            lambda q: ["finance.expenses"])
        monkeypatch.setattr(
            "backend.sql.sql_agent.generate_sql",
            lambda q, t, feedback=None: "SELECT amount FROM finance.expenses")

    def test_policy_rejection_does_not_retry(self, monkeypatch):
        """策略拒绝是终态：SQLAgent 策略链不重试（generate 只调一次），
        不把拒绝原因反馈给模型再生成（§五十二）。"""
        self._patch_agent_chain(monkeypatch)
        agent = get_sql_agent()
        calls = {"n": 0}

        def fake_generate_sql(question, table_names, feedback=None):
            calls["n"] += 1
            assert feedback is None, "策略拒绝不得携带 feedback 重试"
            return "SELECT amount FROM finance.expenses"

        monkeypatch.setattr("backend.sql.sql_agent.generate_sql",
                            fake_generate_sql)
        ctx = make_ctx("department", department="hr")
        result = agent._ask_struct_with_policy(
            "查财务费用", ctx)
        assert calls["n"] == 1
        assert result.status == "permission_denied"

    def test_policy_rejection_error_is_safe(self, monkeypatch):
        """SQLResult.error 对外安全：不包含被拒表名。"""
        self._patch_agent_chain(monkeypatch)
        agent = get_sql_agent()
        ctx = make_ctx("department", department="hr")
        result = agent._ask_struct_with_policy("查财务费用", ctx)
        assert result.status == "permission_denied"
        assert "finance" not in (result.error or "")
        assert "expenses" not in (result.error or "")
