"""SQL Agent 的安全与执行阶段应写入共享 Trace。

P0 约束（2026-10-10）：span.input 不得包含用户原始问题。Trace 详情默认以
detail_level=full 返回（redaction 在 full 下直接 return，不脱敏），且
observability 的 /traces 与 /traces/{id} 端点未强制管理员角色；因此写入
原始问题等同把用户查询明文暴露给任何通过网关验签的用户。
"""

from __future__ import annotations

from types import SimpleNamespace

from backend.observability.tracer import TraceCollector
from backend.sql.sql_result import SQLResult


def test_policy_sql_path_records_stage_spans(monkeypatch):
    import backend.observability.tracer as tracer_module
    import backend.sql.policy as policy_module
    import backend.sql.sql_agent as sql_agent_module

    collector = TraceCollector()
    monkeypatch.setattr(tracer_module, "trace_collector", collector)
    trace = collector.start("查上月销量", "synthetic-session", workflow_name="agent")
    collector.start_span("root", parent_id=None, name="Agent", type="workflow")

    class Guard:
        def precheck(self, _policy):
            return None

        def get_allowed_tables(self, _policy):
            return ["product.products"]

        def validate_and_rewrite(self, _sql, _policy):
            guard_span = tracer_module.trace_collector.start_span(
                "sql.guard", name="sql.guard", type="tool_call",
            )
            tracer_module.trace_collector.end_span(
                guard_span, metrics={"decision": "allow"},
            )
            return SimpleNamespace(
                executable_sql="SELECT sku FROM product.products LIMIT 10",
                params={},
                referenced_tables=["product.products"],
            )

    monkeypatch.setattr(policy_module, "SQLPolicyGuard", Guard)
    monkeypatch.setattr(sql_agent_module, "_prepare_sql_question", lambda question, **_kw: question)
    monkeypatch.setattr(sql_agent_module, "_select_authorized_tables", lambda _q, tables: tables)
    monkeypatch.setattr(sql_agent_module, "generate_sql", lambda *_a, **_kw: "SELECT sku FROM product.products LIMIT 10")
    monkeypatch.setattr(
        sql_agent_module,
        "execute_sql_struct",
        lambda *_a, **_kw: SQLResult.success(
            rows=[{"sku": "SYNTH-001"}], columns=["sku"],
            sql="SELECT sku FROM product.products LIMIT 10", elapsed=0.012,
        ),
    )
    monkeypatch.setattr(sql_agent_module, "_observe", lambda **_kw: None)
    monkeypatch.setattr(sql_agent_module, "emit_sql_stage", lambda *_a, **_kw: None)

    policy = SimpleNamespace(
        user_id="synthetic-admin",
        data_scope="all",
        department="ecom",
        tenant_id="synthetic-tenant",
        source_channel="graph",
    )
    try:
        result = sql_agent_module.SQLAgent(db_config={}, max_retries=0).ask_struct(
            "查上月销量", policy=policy,
        )

        assert result.status == "success"
        spans = {span.span_id: span for span in trace.spans}
        for span_id in (
            "sql.permission_precheck",
            "sql.generate.attempt_1",
            "sql.validate.attempt_1",
            "sql.execute.attempt_1",
        ):
            assert span_id in spans
            assert spans[span_id].status == "success"
        assert spans["sql.generate.attempt_1"].parent_id == "root"
        assert spans["sql.guard"].parent_id == "sql.validate.attempt_1"
        assert spans["sql.execute.attempt_1"].metrics["row_count"] == 1
    finally:
        collector.clear_for_test()


def test_policy_precheck_denial_is_visible_as_rejected_span(monkeypatch):
    import backend.observability.tracer as tracer_module
    import backend.sql.policy as policy_module
    import backend.sql.sql_agent as sql_agent_module

    collector = TraceCollector()
    monkeypatch.setattr(tracer_module, "trace_collector", collector)
    trace = collector.start("查工资数据", "synthetic-session", workflow_name="agent")
    collector.start_span("root", parent_id=None, name="Agent", type="workflow")

    from backend.sql.policy import SQLPolicyError

    class Guard:
        def precheck(self, _policy):
            raise SQLPolicyError("SQL_PERMISSION_DENIED", "synthetic policy rejection")

    monkeypatch.setattr(policy_module, "SQLPolicyGuard", Guard)
    monkeypatch.setattr(sql_agent_module, "_prepare_sql_question", lambda question, **_kw: question)
    monkeypatch.setattr(sql_agent_module, "_observe", lambda **_kw: None)

    policy = SimpleNamespace(
        user_id="synthetic-user",
        data_scope="self",
        department="ecom",
        tenant_id="synthetic-tenant",
        source_channel="graph",
    )
    try:
        result = sql_agent_module.SQLAgent(db_config={}).ask_struct(
            "查工资数据", policy=policy,
        )

        assert result.status == "permission_denied"
        precheck = next(span for span in trace.spans if span.span_id == "sql.permission_precheck")
        assert precheck.status == "rejected"
        assert precheck.metrics["error_code"] == "SQL_PERMISSION_DENIED"
    finally:
        collector.clear_for_test()


def test_sql_stage_spans_never_capture_raw_question(monkeypatch):
    """P0 回归：任何 SQL 阶段 span 的 input 都不得出现用户原始问题。

    该断言锁定“问题原文不进 Trace”这一安全边界；若将来有人把 question
    重新放进 input_data，此测试会立即失败。
    """
    import backend.observability.tracer as tracer_module
    import backend.sql.policy as policy_module
    import backend.sql.sql_agent as sql_agent_module

    secret_question = "查询张三的工资明细和身份证号"
    collector = TraceCollector()
    monkeypatch.setattr(tracer_module, "trace_collector", collector)
    trace = collector.start(secret_question, "synthetic-session", workflow_name="agent")
    collector.start_span("root", parent_id=None, name="Agent", type="workflow")

    class Guard:
        def precheck(self, _policy):
            return None

        def get_allowed_tables(self, _policy):
            return ["product.products"]

        def validate_and_rewrite(self, _sql, _policy):
            return SimpleNamespace(
                executable_sql="SELECT sku FROM product.products LIMIT 10",
                params={},
                referenced_tables=["product.products"],
            )

    monkeypatch.setattr(policy_module, "SQLPolicyGuard", Guard)
    monkeypatch.setattr(sql_agent_module, "_prepare_sql_question", lambda question, **_kw: question)
    monkeypatch.setattr(sql_agent_module, "_select_authorized_tables", lambda _q, tables: tables)
    monkeypatch.setattr(sql_agent_module, "generate_sql", lambda *_a, **_kw: "SELECT sku FROM product.products LIMIT 10")
    monkeypatch.setattr(
        sql_agent_module,
        "execute_sql_struct",
        lambda *_a, **_kw: SQLResult.success(
            rows=[{"sku": "SYNTH-001"}], columns=["sku"],
            sql="SELECT sku FROM product.products LIMIT 10", elapsed=0.012,
        ),
    )
    monkeypatch.setattr(sql_agent_module, "_observe", lambda **_kw: None)
    monkeypatch.setattr(sql_agent_module, "emit_sql_stage", lambda *_a, **_kw: None)

    policy = SimpleNamespace(
        user_id="synthetic-admin",
        data_scope="all",
        department="ecom",
        tenant_id="synthetic-tenant",
        source_channel="graph",
    )
    try:
        result = sql_agent_module.SQLAgent(db_config={}, max_retries=0).ask_struct(
            secret_question, policy=policy,
        )
        assert result.status == "success"

        sql_spans = [s for s in trace.spans if s.span_id.startswith("sql.")]
        assert sql_spans, "应至少产生一个 sql.* 阶段 span"

        for span in sql_spans:
            if not span.input:
                continue
            serialized = repr(span.input)
            assert secret_question not in serialized, (
                f"span {span.span_id} 的 input 泄漏了用户原始问题"
            )
            assert "身份证号" not in serialized
    finally:
        collector.clear_for_test()
