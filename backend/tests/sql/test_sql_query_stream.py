"""SQL Agent 真实执行阶段事件测试。"""

from backend.sql import sql_agent as agent_module
from backend.sql.sql_result import SQLResult
from backend.tests.sql.conftest import build_ctx
from backend.security.principal import Principal
from fastapi.testclient import TestClient


def test_sql_agent_emits_real_progress_phases(monkeypatch):
    events = []
    policy = build_ctx(user_id="3", department="sales", roles=("admin",))

    monkeypatch.setattr(agent_module, "SQL_AGENT_ENABLED", True)
    monkeypatch.setattr(
        agent_module, "select_tables",
        lambda _question: ["product.products"],
    )
    monkeypatch.setattr(
        agent_module, "generate_sql",
        lambda _question, _tables, feedback=None:
        "SELECT product_name FROM product.products LIMIT 5",
    )
    monkeypatch.setattr(
        agent_module, "execute_sql_struct",
        lambda *_args, **_kwargs: SQLResult.success(
            rows=[{"product_name": "A"}],
            columns=["product_name"],
            sql="SELECT product_name FROM product.products LIMIT 5",
        ),
    )

    result = agent_module.SQLAgent({}, max_retries=0).ask_struct(
        "查询商品", policy=policy, event_sink=events.append,
    )

    assert result.status == "success"
    phases = [
        event["data"]["payload"]["phase"]
        for event in events
        if event.get("event") == "log"
    ]
    assert phases == [
        "understanding", "table_routing", "sql_generation",
        "sql_validation", "tool_start", "tool_result",
    ]


def test_http_stream_returns_existing_sse_event_types(monkeypatch):
    import backend.app.api.middleware.auth as auth_mw
    import backend.app.api.routes.sql as sql_route
    from backend.app.server import app

    monkeypatch.setattr(auth_mw, "API_KEY", "test")
    monkeypatch.setattr(sql_route, "_require_sql_enabled", lambda: None)
    monkeypatch.setattr(sql_route, "load_sql_query_context", lambda *_args: None)
    monkeypatch.setattr(sql_route, "persist_sql_query_result", lambda **_kwargs: {})
    monkeypatch.setattr(
        sql_route, "get_sql_agent",
        lambda: type("FakeAgent", (), {
            "ask_struct": lambda self, question, policy=None, event_sink=None:
            (
                event_sink({"event": "log", "data": {
                    "node": "sql_executor", "step_id": "tool_result",
                    "message": "查询完成", "payload": {
                        "phase": "tool_result", "tool": "sql.query",
                    }, "ts": 0.0,
                }}) if event_sink else None
            ) or SQLResult.success(
                rows=[{"product_name": "A"}],
                columns=["product_name"],
                sql="SELECT product_name FROM product.products LIMIT 5",
            ),
        })(),
    )
    principal = Principal(
        user_id="3", tenant_id="tenant-a", department="sales",
        roles=("admin",), authenticated=True, subject_type="employee",
    )
    app.dependency_overrides[sql_route.get_principal] = lambda: principal
    try:
        response = TestClient(app).post(
            "/sql/query/stream",
            json={"question": "查询商品", "session_id": "stream-test"},
            headers={"X-API-Key": "test"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert "event: meta" in response.text
    assert "event: status" in response.text
    assert "event: log" in response.text
    assert "event: done" in response.text
