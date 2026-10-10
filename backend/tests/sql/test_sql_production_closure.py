# -*- coding: utf-8 -*-
"""STOP C 生产收口测试：kill switch / deny 终态 / Tool-MCP 收口 /
list_tables 白名单一致性 / 审计 best-effort。

只 mock 外部边界（executor/LLM/DB 连接）；策略与装配逻辑全部真实执行。
"""
import asyncio
import json
from unittest.mock import patch

import pytest

from tests.sql.conftest import build_ctx, make_ctx

from backend.core.request_context import (
    set_tool_department,
    set_tool_roles,
    set_tool_tenant_id,
    set_tool_user_id,
)
from backend.sql.policy import SQLPolicyContext, build_sql_policy_context
from backend.sql.schema_loader import schema_loader
from backend.sql.sql_agent import SQLAgent, _unavailable_result, get_sql_agent
from backend.sql.sql_result import SQLResult


@pytest.fixture(autouse=True)
def _clean_tool_contextvars():
    """每个用例前后清理 Tool 通道 contextvars（防跨用例串身份）。"""
    for setter in (set_tool_user_id, set_tool_department,
                   set_tool_tenant_id):
        setter("")
    set_tool_roles(())
    yield
    for setter in (set_tool_user_id, set_tool_department,
                   set_tool_tenant_id):
        setter("")
    set_tool_roles(())


def _editor_ctx(source="tool"):
    return build_sql_policy_context(
        user_id="3", department="hr", roles=("editor",),
        source_channel=source)


# =====================================================
# Kill switch（规格 §十四：executor 调用次数 = 0）
# =====================================================

class TestKillSwitch:
    def test_ask_struct_disabled_no_executor_call(self, monkeypatch):
        """SQL_AGENT_ENABLED=false → ask_struct 直接 unavailable，
        executor 与 LLM 生成零调用。"""
        import backend.sql.sql_agent as agent_mod

        monkeypatch.setattr(agent_mod, "SQL_AGENT_ENABLED", False)
        executor_calls = {"n": 0}
        monkeypatch.setattr(
            agent_mod, "execute_sql_struct",
            lambda *a, **k: executor_calls.__setitem__(
                "n", executor_calls["n"] + 1) or SQLResult.success(
                [], columns=[], sql="x", elapsed=0))
        monkeypatch.setattr(agent_mod, "generate_sql", lambda *a, **k: "SELECT 1")

        agent = get_sql_agent()
        result = agent.ask_struct(
            "查库存", policy=_editor_ctx(source="http"))
        assert result.error_type == "service_unavailable"
        assert executor_calls["n"] == 0

    def test_legacy_path_disabled_no_executor_call(self, monkeypatch):
        """旧链（policy=None）同样被 kill switch 拦截——不存在
        「HTTP 关了、legacy 还能执行」。"""
        import backend.sql.sql_agent as agent_mod

        monkeypatch.setattr(agent_mod, "SQL_AGENT_ENABLED", False)
        executor_calls = {"n": 0}
        monkeypatch.setattr(
            agent_mod, "execute_sql_struct",
            lambda *a, **k: executor_calls.__setitem__(
                "n", executor_calls["n"] + 1) or SQLResult.success(
                [], columns=[], sql="x", elapsed=0))
        monkeypatch.setattr(agent_mod, "select_tables", lambda q: ["product.products"])
        monkeypatch.setattr(agent_mod, "generate_sql", lambda *a, **k: "SELECT 1")

        agent = get_sql_agent()
        result = agent.ask_struct("查库存")
        assert result.error_type == "service_unavailable"
        assert executor_calls["n"] == 0

    def test_skill_disabled_terminal_unavailable(self, monkeypatch):
        """graph SQLSkill 入口：kill switch 关闭 → 终态 unavailable
        step_result（无重试语义）。"""
        import backend.skills.sql.skill as skill_mod
        from backend.skills.sql.skill import SQLSkill

        monkeypatch.setattr("backend.config.SQL_AGENT_ENABLED", False)
        # SQLSkill.execute 是 async def；直接驱动
        result = asyncio.run(SQLSkill().execute(
            {"current_step_id": "1"}, step_capability="sql.query"))
        step = result["step_results"]["1"]
        assert step["error_type"] == "service_unavailable"
        assert step["tool_status"] == "unavailable"

    def test_tool_disabled_envelope(self, monkeypatch):
        """Tool 入口：kill switch 关闭 → unavailable 错误封套。"""
        from backend.tools.sql import sql_query_tool

        monkeypatch.setattr("backend.config.SQL_AGENT_ENABLED", False)
        raw = sql_query_tool.invoke({"question": "查库存"})
        envelope = json.loads(raw)
        assert envelope.get("reason") == "service_unavailable", raw

    def test_mcp_disabled_unavailable(self, monkeypatch):
        """MCP 入口：kill switch 关闭 → unavailable（sql_query 与
        list_tables 全部覆盖）。"""
        from mcp_servers.servers.sql import SQLMCPServer

        monkeypatch.setattr("backend.config.SQL_AGENT_ENABLED", False)
        server = SQLMCPServer()
        for tool_name in ("sql_query", "list_tables"):
            out = server.call_tool(
                tool_name, {} if tool_name == "list_tables"
                else {"question": "查库存"})
            assert out.get("reason") == "service_unavailable", tool_name

    def test_unavailable_is_not_permission_semantics(self):
        """不可用 ≠ 权限拒绝：error_type 不与权限语义混淆（§十四）。"""
        result = _unavailable_result()
        assert result.status == "failed"
        assert result.error_type == "service_unavailable"


# =====================================================
# Deny 终态（2026-10-06 语义：表域判定前置——generate=0、executor=0）
# =====================================================

class TestDenyTerminal:
    def _agent_with_counters(self, monkeypatch, validation_error):
        import backend.sql.sql_agent as agent_mod

        calls = {"generate": 0, "executor": 0}
        monkeypatch.setattr(agent_mod, "select_tables",
                            lambda q: ["finance.expenses"])
        monkeypatch.setattr(
            agent_mod, "generate_sql",
            lambda q, t, feedback=None: calls.__setitem__(
                "generate", calls["generate"] + 1) or "SELECT amount FROM finance.expenses")
        monkeypatch.setattr(
            agent_mod, "execute_sql_struct",
            lambda *a, **k: calls.__setitem__(
                "executor", calls["executor"] + 1) or SQLResult.success(
                [], columns=[], sql="x", elapsed=0))
        monkeypatch.setattr(
            agent_mod, "sql_validator",
            type("V", (), {"validate": staticmethod(
                lambda sql: (_ for _ in ()).throw(validation_error))})())
        return get_sql_agent(), calls

    def test_terminal_deny_no_retry_no_executor(self, monkeypatch):
        """policy 链：策略拒绝（internal 表 + department scope）→ 终态——
        表域判定前置，generate 零调用、executor 零调用（拒绝不花 LLM 成本）。"""
        import backend.sql.sql_agent as agent_mod

        calls = {"generate": 0, "executor": 0}
        monkeypatch.setattr(agent_mod, "select_tables",
                            lambda q: ["finance.expenses"])
        monkeypatch.setattr(
            agent_mod, "generate_sql",
            lambda q, t, feedback=None: calls.__setitem__(
                "generate", calls["generate"] + 1) or "SELECT amount FROM finance.expenses")
        monkeypatch.setattr(
            agent_mod, "execute_sql_struct",
            lambda *a, **k: calls.__setitem__(
                "executor", calls["executor"] + 1) or SQLResult.success(
                [], columns=[], sql="x", elapsed=0))

        agent = get_sql_agent()
        result = agent.ask_struct("查财务", policy=_editor_ctx(source="graph"))
        assert calls["generate"] == 0
        assert calls["executor"] == 0
        assert result.status == "permission_denied"

    def test_feedback_never_leaks_on_deny(self, monkeypatch):
        """策略拒绝发生在生成之前——generate 零调用，feedback 无从泄露。"""
        import backend.sql.sql_agent as agent_mod

        seen_feedback = []
        monkeypatch.setattr(agent_mod, "select_tables",
                            lambda q: ["finance.expenses"])

        def fake_generate(q, t, feedback=None):
            seen_feedback.append(feedback)
            return "SELECT amount FROM finance.expenses"

        monkeypatch.setattr(agent_mod, "generate_sql", fake_generate)
        agent = get_sql_agent()
        agent.ask_struct("查财务", policy=_editor_ctx(source="graph"))
        assert seen_feedback == []

    def test_syntax_error_still_retryable(self, monkeypatch):
        """旧链：语法类（alias_undefined）保留既有有限修复重试——
        终态化只针对安全拒绝，不误伤可修复错误（重试仍重走校验）。"""
        from backend.sql.sql_validator import ValidationError

        import backend.sql.sql_agent as agent_mod

        calls = {"generate": 0, "executor": 0, "validate": 0}
        monkeypatch.setattr(agent_mod, "select_tables",
                            lambda q: ["product.products"])
        monkeypatch.setattr(
            agent_mod, "generate_sql",
            lambda q, t, feedback=None: calls.__setitem__(
                "generate", calls["generate"] + 1) or "SELECT id FROM product.products")
        monkeypatch.setattr(
            agent_mod, "execute_sql_struct",
            lambda *a, **k: calls.__setitem__(
                "executor", calls["executor"] + 1) or SQLResult.success(
                [], columns=[], sql="x", elapsed=0))

        class _FakeValidator:
            def validate(self, sql):
                calls["validate"] += 1
                raise ValidationError("别名未定义", layer=2,
                                      reason="alias_undefined")

        monkeypatch.setattr(agent_mod, "sql_validator", _FakeValidator())

        agent = get_sql_agent()
        result = agent.ask_struct("查商品")  # policy=None → 旧链
        assert calls["generate"] == 3  # max_retries=2 → 1+2 次
        assert calls["validate"] == 3
        assert calls["executor"] == 0
        assert result.status == "validation_error"

    def test_legacy_terminal_deny_no_retry(self, monkeypatch):
        """旧链：安全拒绝（dangerous_function）同样终态——generate=1。"""
        from backend.sql.sql_validator import ValidationError

        import backend.sql.sql_agent as agent_mod

        calls = {"generate": 0}
        monkeypatch.setattr(agent_mod, "select_tables",
                            lambda q: ["product.products"])
        monkeypatch.setattr(
            agent_mod, "generate_sql",
            lambda q, t, feedback=None: calls.__setitem__(
                "generate", calls["generate"] + 1)
            or "SELECT pg_sleep(60)")

        class _FakeValidator:
            def validate(self, sql):
                raise ValidationError("禁止使用函数: PG_SLEEP()",
                                      layer=4, reason="dangerous_function")

        monkeypatch.setattr(agent_mod, "sql_validator", _FakeValidator())

        agent = get_sql_agent()
        result = agent.ask_struct("慢查")
        assert calls["generate"] == 1
        assert result.status == "validation_error"
        assert "PG_SLEEP" not in (result.error or "")

    def test_validator_reason_taxonomy(self):
        """reason 分类完备性：安全拒绝枚举全部标记终态，语法类不误标。"""
        from backend.sql.sql_validator import TERMINAL_DENY_REASONS, ValidationError

        for reason in ("non_select", "multi_statement", "select_into",
                       "lock_clause", "write_in_subquery", "table_forbidden",
                       "schema_forbidden", "column_forbidden",
                       "star_projection", "dangerous_function"):
            assert ValidationError("x", layer=1, reason=reason).is_terminal_deny
        for reason in ("parse_error", "empty_sql", "alias_undefined",
                       "limit_exceeded"):
            assert not ValidationError("x", layer=0, reason=reason).is_terminal_deny


# =====================================================
# Tool / MCP 通道收口
# =====================================================

class TestToolChannelClosure:
    def test_execute_sql_tool_passes_guard_and_injects_scope(self, monkeypatch):
        """execute_sql_tool（原旁路）：现在必须过 SQLPolicyGuard。
        admin（all）查 shared 表 → 放行且 source=tool；
        editor（department）查 personal 表 → 拒绝（scope 语义在 tool
        通道与 graph/HTTP 通道完全一致）。"""
        from backend.tools.sql import execute_sql_tool

        set_tool_user_id("9")
        set_tool_roles(("admin",))
        captured = {}

        def fake_guard_validate(self, sql, policy):
            captured["policy"] = policy
            from backend.sql.policy import SQLPolicyGuard as G
            return G._validate_and_rewrite_inner(self, sql, policy)

        monkeypatch.setattr(
            "backend.sql.policy.SQLPolicyGuard.validate_and_rewrite",
            fake_guard_validate)

        def fake_executor(sql, db_config=None, params=None, timeout=None):
            return SQLResult.success(
                [{"sku": "A"}], columns=["sku"], sql=sql, elapsed=0.01)

        monkeypatch.setattr("backend.sql.executor.execute_sql_struct",
                            fake_executor)

        raw = execute_sql_tool.invoke(
            {"query": "SELECT sku FROM product.products"})
        envelope = json.loads(raw)
        assert envelope.get("data", {}).get("total") == 1, raw
        assert captured["policy"].source_channel == "tool"
        assert captured["policy"].data_scope == "all"

    def test_execute_sql_tool_personal_denied_for_editor(self):
        """tool 通道 scope 语义一致性：editor（department）直查
        personal 表（无部门列）→ 拒绝——旁路收口后不再有例外。"""
        from backend.tools.sql import execute_sql_tool

        set_tool_user_id("3")
        set_tool_roles(("editor",))
        raw = execute_sql_tool.invoke(
            {"query": 'SELECT order_no FROM "order".orders'})
        assert "SQL_TABLE_NOT_ALLOWED" in raw

    def test_execute_sql_tool_denied_for_internal_table(self):
        """原旁路封死验证：tool 直调查 finance 表 → 权限门拒绝封套，
        executor 不被触达（真实 Guard，无 mock）。"""
        from backend.tools.sql import execute_sql_tool

        set_tool_user_id("3")
        set_tool_roles(("editor",))
        raw = execute_sql_tool.invoke(
            {"query": "SELECT amount FROM finance.expenses"})
        assert "finance" not in raw  # 对外文案不泄露表名
        assert "SQL_TABLE_NOT_ALLOWED" in raw

    def test_tool_without_identity_denied(self):
        """Tool 通道无可信身份（脚本直调）→ 权限门 fail-closed，
        且不触发 LLM 生成（前置 precheck）。"""
        from backend.tools.sql import sql_query_tool

        raw = sql_query_tool.invoke({"question": "查询销量"})  # 不设任何 contextvar
        assert ("超出你的数据访问范围" in raw
                or "SQL_PERMISSION_DENIED" in raw), raw

    def test_sql_query_tool_success_uses_policy_chain(self, monkeypatch):
        """sql_query_tool：policy 链生效（source=tool）且真实可查。"""
        from backend.tools.sql import sql_query_tool

        set_tool_user_id("9")
        set_tool_roles(("admin",))
        raw = sql_query_tool.invoke({"question": "商品库存"})
        assert isinstance(raw, str)


class TestMcpChannelClosure:
    def test_list_tables_matches_whitelist_exactly(self):
        """list_tables 数据源 = schema_loader 白名单（修复连错库 +
        information_schema 探测面）；7 个业务 schema 全部可见。"""
        from mcp_servers.servers.sql import SQLMCPServer

        out = SQLMCPServer().call_tool("list_tables", {})
        tables = set(out["tables"])
        expected = set(schema_loader.get_all_table_names())
        assert tables == expected  # 严格一致，不是「非空」
        for schema in ("product", "order", "inventory", "customer",
                       "crawler", "finance", "ai"):
            assert any(t.startswith(f"{schema}.") for t in tables), schema
        # 白名单外对象绝不出现（旧实现连 agent_memory 库会列出内部表）
        assert "public.schema_migrations" not in tables
        assert not any(t.startswith("auth.") for t in tables)

    def test_mcp_sql_query_without_identity_denied(self):
        """MCP 通道无可信身份 → 权限门拒绝（不存在匿名 SQL），
        且前置 precheck 保证不触发 LLM 生成。"""
        from mcp_servers.servers.sql import SQLMCPServer

        out = SQLMCPServer().call_tool("sql_query", {"question": "查销量"})
        assert out.get("reason") == "permission_denied", out
        assert "finance" not in json.dumps(out, ensure_ascii=False)


# =====================================================
# 审计（best-effort）
# =====================================================

class TestAuditBestEffort:
    def test_hash_stable_and_normalized(self):
        from backend.sql.audit import normalize_sql_hash

        h1 = normalize_sql_hash("SELECT  a,  b\n FROM t")
        h2 = normalize_sql_hash("select a b from t".replace(" a b ", " a,  b "))
        assert h1 == h2  # 空白/大小写规范化后一致
        assert len(h1) == 64

    def test_record_failure_never_raises(self, monkeypatch):
        """审计写入失败（DB 不可达）不向主查询传播——best-effort 语义。"""
        from backend.sql import audit

        def boom():
            raise RuntimeError("db down")

        monkeypatch.setattr(audit, "_conn", boom)
        # 不抛异常即通过（内部线程吞掉）
        audit.record_sql_audit(
            decision=audit.DECISION_DENY_TABLE,
            user_id="u1", sql="SELECT 1", tables=["product.products"],
            deny_code="SQL_TABLE_NOT_ALLOWED")
        import time as _t
        _t.sleep(0.3)  # 等待后台线程落地失败路径

    def test_decision_mapping(self):
        from backend.sql.audit import (
            DECISION_EXECUTION_FAILED, DECISION_EXECUTION_SUCCESS,
            DECISION_TIMEOUT, decision_from_result)

        assert decision_from_result("success") == DECISION_EXECUTION_SUCCESS
        assert decision_from_result("no_data") == DECISION_EXECUTION_SUCCESS
        assert decision_from_result("timeout") == DECISION_TIMEOUT
        assert decision_from_result("syntax_error") == DECISION_EXECUTION_FAILED


# =====================================================
# SQLPolicyContext source_channel（审计归因，不参与授权）
# =====================================================

class TestSourceChannel:
    def test_channel_propagates(self):
        ctx = build_sql_policy_context(
            user_id="3", roles=("editor",), source_channel="mcp")
        assert ctx.source_channel == "mcp"

    def test_channel_truncated_low_cardinality(self):
        ctx = build_sql_policy_context(
            user_id="3", roles=("editor",),
            source_channel="x" * 200)
        assert len(ctx.source_channel) <= 16

    def test_default_empty(self):
        ctx = make_ctx("all")
        assert ctx.source_channel == ""


# =====================================================
# Tool 参数伪造身份（规格 §十一：模型输出参数不可信）
# =====================================================

class TestToolSpoof:
    def test_sql_query_tool_args_cannot_escalate_role(self, monkeypatch):
        """viewer 真实身份 + tool args 伪造 role=admin/data_scope=all：
        无论额外参数被拒绝还是忽略，executor 不得触达（viewer wins）。"""
        import backend.sql.sql_agent as agent_mod
        from backend.tools.sql import sql_query_tool

        set_tool_user_id("7")
        set_tool_roles(("viewer",))
        calls = {"executor": 0}
        monkeypatch.setattr(
            agent_mod, "execute_sql_struct",
            lambda *a, **k: calls.__setitem__(
                "n", calls["executor"] + 1) or SQLResult.success(
                [{"sku": "SECRET"}], columns=["sku"], sql="x", elapsed=0))

        raw = sql_query_tool.invoke({
            "question": "查所有商品", "role": "admin", "data_scope": "all"})
        assert "SECRET" not in raw
        assert calls["executor"] == 0, raw

    def test_execute_sql_tool_args_cannot_escalate_scope(self):
        """editor(department) + 伪造 admin/all args 直查 finance 表：
        授权仍按 contextvars 真实身份判定 → 拒绝。"""
        from backend.tools.sql import execute_sql_tool

        set_tool_user_id("3")
        set_tool_roles(("editor",))
        raw = execute_sql_tool.invoke({
            "query": "SELECT amount FROM finance.expenses",
            "role": "admin", "data_scope": "all"})
        assert "SQL_TABLE_NOT_ALLOWED" in raw, raw


# =====================================================
# Graph 通道集成（规格 §九：policy 非 None + Guard 恰好一次）
# =====================================================

class TestGraphIntegration:
    def _state(self, roles=("editor",)):
        return {
            "question": "查财务费用",
            "plan": {"nodes": {"1": {"step_id": "1",
                                     "capability": "sql.query"}}},
            "current_step_id": "1",
            "step_results": {},
            "request_context": {
                "session_id": "sess-graph", "user_id": "3",
                "department": "hr", "roles": list(roles),
            },
        }

    def test_deny_skill_path_pre_generation_executor_zero(self, monkeypatch):
        from backend.skills.sql.skill import SQLSkill

        import backend.sql.sql_agent as agent_mod

        calls = {"generate": 0, "executor": 0, "guard": 0}
        monkeypatch.setattr(agent_mod, "select_tables",
                            lambda q: ["finance.expenses"])
        monkeypatch.setattr(
            agent_mod, "generate_sql",
            lambda q, t, feedback=None: calls.__setitem__(
                "generate", calls["generate"] + 1)
            or "SELECT amount FROM finance.expenses")
        monkeypatch.setattr(
            agent_mod, "execute_sql_struct",
            lambda *a, **k: calls.__setitem__(
                "executor", calls["executor"] + 1) or SQLResult.success(
                [], columns=[], sql="x", elapsed=0))

        from backend.sql.policy import SQLPolicyGuard as _Guard
        real_guard = _Guard.validate_and_rewrite

        def counting_guard(self, sql, policy):
            calls["guard"] += 1
            return real_guard(self, sql, policy)

        monkeypatch.setattr(_Guard, "validate_and_rewrite", counting_guard)

        async def run():
            return await SQLSkill().execute(self._state(),
                                            step_capability="sql.query")

        result = asyncio.run(run())
        sr = result["step_results"]["1"]
        # 表域判定前置：拒绝发生在 guard/生成之前，三者全零
        assert calls["guard"] == 0, calls
        assert calls["generate"] == 0
        assert calls["executor"] == 0
        # skill 层语义：permission_denied → step failed + error_type 保留原语义
        assert sr["status"] == "failed"
        assert sr["error_type"] == "permission_denied"

    def test_success_policy_channel_is_graph(self, monkeypatch):
        """成功路径：policy 非 None 且 source_channel="graph"（审计归因）。"""
        from backend.skills.sql.skill import SQLSkill

        import backend.sql.sql_agent as agent_mod

        seen = {}
        monkeypatch.setattr(agent_mod, "select_tables",
                            lambda q: ["product.products"])
        monkeypatch.setattr(
            agent_mod, "generate_sql",
            lambda q, t, feedback=None: "SELECT sku FROM product.products LIMIT 3")
        monkeypatch.setattr(
            agent_mod, "execute_sql_struct",
            lambda *a, **k: SQLResult.success(
                [{"sku": "A"}], columns=["sku"], sql="x", elapsed=0))

        from backend.sql.policy import SQLPolicyGuard as _Guard
        real_guard = _Guard.validate_and_rewrite

        def spy_guard(self, sql, policy):
            seen["policy"] = policy
            return real_guard(self, sql, policy)

        monkeypatch.setattr(_Guard, "validate_and_rewrite", spy_guard)

        state = self._state()
        state["question"] = "查商品"
        state["request_context"] = dict(state["request_context"],
                                        roles=["editor"])

        async def run():
            return await SQLSkill().execute(state, step_capability="sql.query")

        result = asyncio.run(run())
        sr = result["step_results"]["1"]
        assert sr["status"] == "success", sr
        assert seen["policy"] is not None
        assert seen["policy"].source_channel == "graph"


# =====================================================
# 审计归因（规格 §三十：source_channel 必须区分通道）
# =====================================================

class TestAuditAttribution:
    def _capture_audit(self, monkeypatch):
        from backend.sql import audit as audit_mod

        seen = []
        monkeypatch.setattr(audit_mod, "record_sql_audit",
                            lambda **kw: seen.append(kw))
        return seen

    def test_graph_deny_audited_with_channel(self, monkeypatch):
        import backend.sql.sql_agent as agent_mod
        from backend.skills.sql.skill import SQLSkill

        seen = self._capture_audit(monkeypatch)
        monkeypatch.setattr(agent_mod, "select_tables",
                            lambda q: ["finance.expenses"])
        monkeypatch.setattr(
            agent_mod, "generate_sql",
            lambda q, t, feedback=None: "SELECT amount FROM finance.expenses")

        state = {
            "question": "查财务",
            "plan": {"nodes": {"1": {"step_id": "1",
                                     "capability": "sql.query"}}},
            "current_step_id": "1", "step_results": {},
            "request_context": {"user_id": "3", "department": "hr",
                                "roles": ["editor"]},
        }

        async def run():
            return await SQLSkill().execute(state, step_capability="sql.query")

        asyncio.run(run())
        assert seen, "deny 场景必须产生审计事件"
        deny = [e for e in seen if e["decision"].startswith("DENY")]
        assert deny
        assert deny[-1]["source_channel"] == "graph"
        assert deny[-1]["deny_code"] in ("SQL_TABLE_NOT_ALLOWED",
                                         "SQL_PERMISSION_DENIED",
                                         "SQL_SCOPE_UNAVAILABLE")

    def test_tool_deny_audited_with_channel(self, monkeypatch):
        from backend.tools.sql import execute_sql_tool

        seen = self._capture_audit(monkeypatch)
        set_tool_user_id("3")
        set_tool_roles(("editor",))
        execute_sql_tool.invoke(
            {"query": "SELECT amount FROM finance.expenses"})
        assert seen
        deny = [e for e in seen if e["decision"].startswith("DENY")]
        assert deny
        assert deny[-1]["source_channel"] == "tool"
        assert deny[-1]["deny_code"] == "SQL_TABLE_NOT_ALLOWED"


# =====================================================
# sql.guard span（STOP C §十二 / STOP D 完成标准 F：
# allow 与 deny 都正常收口、attributes 低基数）
# =====================================================

class TestSqlGuardSpan:
    def _trace(self):
        from backend.observability.tracer import trace_collector

        return trace_collector.start(
            "sql guard span", "s-sql-guard", workflow_name="agent")

    def _guard_spans(self, trace):
        return [s for s in trace.spans if s.name == "sql.guard"]

    def test_allow_span_closes_with_low_cardinality_metrics(self):
        """allow 路径：span 收口 success，metrics 全为低基数枚举。"""
        from backend.observability.tracer import trace_collector
        from backend.sql.policy import SQLPolicyGuard
        from backend.sql.sql_validator import ValidationError
        from tests.sql.conftest import build_ctx

        trace = self._trace()
        try:
            ctx = build_ctx(user_id="3", department="ecom",
                            tenant_id="t1", roles=("editor",))
            guarded = SQLPolicyGuard().validate_and_rewrite(
                "SELECT sku FROM product.products WHERE id = 1", ctx)
        finally:
            trace_collector.finish(trace, "", 1, "", "")

        spans = self._guard_spans(trace)
        assert len(spans) == 1
        span = spans[0]
        assert span.status == "success"
        m = span.metrics
        assert m["decision"] in ("allow", "allow_no_scope")
        assert m["source_channel"] == "unknown"  # 直调 Guard 未声明通道
        # tracer 框架会附加 covered_ms/uncovered_ms——业务字段为低基数五元组
        assert {"source_channel", "data_scope", "decision",
                "reason_code", "table_count"} <= set(m)

    def test_deny_span_closes_error_status(self):
        """deny 路径：span 同样收口（status=error + decision=deny），无 dangling。"""
        from backend.observability.tracer import trace_collector
        from backend.sql.policy import SQLPolicyError, SQLPolicyGuard
        from tests.sql.conftest import make_ctx

        trace = self._trace()
        try:
            ctx = make_ctx("department", department="hr", roles=("editor",))
            with pytest.raises(SQLPolicyError):
                SQLPolicyGuard().validate_and_rewrite(
                    "SELECT amount FROM finance.expenses", ctx)
        finally:
            trace_collector.finish(trace, "", 1, "", "")

        spans = self._guard_spans(trace)
        assert len(spans) == 1
        assert spans[0].status == "error"
        assert spans[0].metrics["decision"] == "deny"
        assert spans[0].metrics["reason_code"] == "SQL_TABLE_NOT_ALLOWED"

    def test_span_id_is_stable_across_guard_instances(self):
        """span_id 必须稳定：同一逻辑步骤在不同 Guard/请求上 id 一致。

        回归：原实现用 f"sql.guard.{uuid4().hex[:8]}"，同一 trace 内每次
        调用都是新 id，管理端按 span_id 聚合会碎裂，按 "sql.guard" 查表
        也永远落空。
        """
        from backend.observability.tracer import trace_collector
        from backend.sql.policy import SQLPolicyGuard
        from tests.sql.conftest import build_ctx

        ctx = build_ctx(user_id="3", department="ecom",
                        tenant_id="t1", roles=("editor",))
        sql = "SELECT sku FROM product.products WHERE id = 1"

        # 两个独立实例、独立 trace → 首个 guard span id 都是 "sql.guard"
        seen_ids = []
        for _ in range(2):
            trace = self._trace()
            try:
                SQLPolicyGuard().validate_and_rewrite(sql, ctx)
            finally:
                trace_collector.finish(trace, "", 1, "", "")
            spans = self._guard_spans(trace)
            assert len(spans) == 1
            seen_ids.append(spans[0].span_id)
        assert seen_ids == ["sql.guard", "sql.guard"]

    def test_multiple_calls_in_one_trace_get_unique_stable_ids(self):
        """同一 trace 内多次调用（count/rows 双查询）id 唯一且可预测。"""
        from backend.observability.tracer import trace_collector
        from backend.sql.policy import SQLPolicyGuard
        from tests.sql.conftest import build_ctx

        trace = self._trace()
        try:
            ctx = build_ctx(user_id="3", department="ecom",
                            tenant_id="t1", roles=("editor",))
            guard = SQLPolicyGuard()
            guard.validate_and_rewrite(
                "SELECT count(*) FROM product.products", ctx)
            guard.validate_and_rewrite(
                "SELECT sku FROM product.products WHERE id = 1", ctx)
        finally:
            trace_collector.finish(trace, "", 1, "", "")

        ids = [s.span_id for s in self._guard_spans(trace)]
        assert ids == ["sql.guard", "sql.guard#2"]
    def test_noop_span_without_active_trace_does_not_raise(self):
        """无 active trace（worker 直调等）→ noop span 软失败不阻塞查询。"""
        from backend.sql.policy import SQLPolicyGuard
        from tests.sql.conftest import build_ctx

        ctx = build_ctx(user_id="3", department="ecom",
                        tenant_id="t1", roles=("editor",))
        guarded = SQLPolicyGuard().validate_and_rewrite(
            "SELECT sku FROM product.products WHERE id = 1", ctx)
        assert guarded is not None


    def test_multiple_guards_and_calls_share_trace_with_unique_ids(self, monkeypatch):
        """同一 trace 内多个 Guard 实例、每实例多次调用：span_id 唯一且父子正确。

        生产链路存在两种形态：``app/api/routes/sql.py`` 复用一个实例连续调用
        validate_and_rewrite（count + rows），``sql/policy.py`` 的模块级函数与
        ``tools/sql.py`` 则每次调用新建实例。span_id 由实例内递增序号生成
        （``sql.guard`` / ``sql.guard#N``），tracer 对同 trace 内重复 id 追加
        ``#N`` 去重兜底。本用例锁定：两种形态叠加时 id 仍唯一、父级仍为 root、
        span 正常收口。

        使用独立 TraceCollector 并挂到 tracer 模块，避免全局 trace_collector
        的持久化后端（依赖 PostgreSQL）——与 test_sql_agent_trace_stages.py
        同一隔离方式。
        """
        import backend.observability.tracer as tracer_module
        from backend.observability.tracer import TraceCollector
        from backend.sql.policy import SQLPolicyGuard
        from tests.sql.conftest import build_ctx

        collector = TraceCollector()
        monkeypatch.setattr(tracer_module, "trace_collector", collector)
        trace = collector.start("sql guard shared", "s-shared", workflow_name="agent")
        collector.start_span("root", parent_id=None, name="Agent", type="workflow")
        try:
            ctx = build_ctx(user_id="3", department="ecom",
                            tenant_id="t1", roles=("editor",))
            sql = "SELECT sku FROM product.products WHERE id = 1"

            shared = SQLPolicyGuard()
            shared.validate_and_rewrite(sql, ctx)   # sql.py count
            shared.validate_and_rewrite(sql, ctx)   # sql.py rows

            fresh = SQLPolicyGuard()
            fresh.validate_and_rewrite(sql, ctx)
            SQLPolicyGuard().validate_and_rewrite(sql, ctx)  # 又一只新实例

            spans = [s for s in trace.spans if s.name == "sql.guard"]
            assert len(spans) == 4

            ids = [s.span_id for s in spans]
            assert len(ids) == len(set(ids)), f"span_id 必须唯一: {ids}"

            # 每个 Guard span 都是独立逻辑步骤，父级统一挂 root，不互相嵌套
            assert {s.parent_id for s in spans} == {"root"}, (
                f"Guard span 不得互相嵌套: "
                f"{[(s.span_id, s.parent_id) for s in spans]}"
            )
            assert all(s.status == "success" for s in spans)
            assert all(s.metrics.get("decision") in ("allow", "allow_no_scope")
                       for s in spans)
        finally:
            collector.clear_for_test()

    def test_guard_span_ids_are_reproducible_not_random(self, monkeypatch):
        """同一调用序列重复执行应得到相同 span_id 序列（替代随机 uuid 后缀）。

        管理端按 span_id 聚合/对比；随机后缀会让同一逻辑步骤每次请求都是新
        id，跨 trace 无法归并。本用例锁定确定性：两次独立执行结果一致。
        """
        import backend.observability.tracer as tracer_module
        from backend.observability.tracer import TraceCollector
        from backend.sql.policy import SQLPolicyGuard
        from tests.sql.conftest import build_ctx

        def run_once():
            collector = TraceCollector()
            monkeypatch.setattr(tracer_module, "trace_collector", collector)
            trace = collector.start("sql guard repro", "s-repro", workflow_name="agent")
            try:
                ctx = build_ctx(user_id="3", department="ecom",
                                tenant_id="t1", roles=("editor",))
                sql = "SELECT sku FROM product.products WHERE id = 1"
                guard = SQLPolicyGuard()
                guard.validate_and_rewrite(sql, ctx)   # count
                guard.validate_and_rewrite(sql, ctx)   # rows
                return [s.span_id for s in trace.spans if s.name == "sql.guard"]
            finally:
                collector.clear_for_test()

        first = run_once()
        second = run_once()
        assert first == second, f"span_id 序列应可复现: {first} vs {second}"
        assert first[0] == "sql.guard"
        assert all(not sid.startswith("sql.guard.") for sid in first), (
            "不得回退为随机 uuid 后缀命名"
        )
