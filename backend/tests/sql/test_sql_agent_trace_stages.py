"""SQL Agent 的安全与执行阶段应写入共享 Trace。"""

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
            "sql.table_router",
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
