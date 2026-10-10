"""用户端 SQL Skill 复用会话 SQL 上下文测试。"""

import asyncio

from backend.sql.query_context import SQLQueryContext, permission_fingerprint
from backend.sql.sql_result import SQLResult
from backend.skills.sql.skill import SQLSkill
from backend.tests.sql.conftest import build_ctx


def test_sql_skill_passes_previous_query_context_to_agent(monkeypatch):
    seen = {}
    policy = build_ctx(user_id="3", department="sales", roles=("admin",))
    previous = SQLQueryContext(
        question="统计各商品分类的销售额排名",
        standalone_question="统计各商品分类的销售额排名",
        tables=("product.categories", "product.products", "order.order_items"),
        permission_fingerprint=permission_fingerprint(policy),
    )

    class FakeAgent:
        # 签名须与 SQLAgent.ask_struct 对齐：Skill 现在会多传 event_sink
        # （SSE 进度事件）。缺该参数会让 to_thread 调用直接 TypeError，
        # 被 Skill 吞成查询失败，表现为 seen["question"] KeyError。
        def ask_struct(self, question, policy=None, query_context=None,
                       event_sink=None):
            seen["question"] = question
            seen["query_context"] = query_context
            seen["event_sink"] = event_sink
            return SQLResult.success(
                rows=[{"product_name": "A"}],
                columns=["product_name"],
                sql="SELECT product_name FROM product.products LIMIT 5",
            )

    monkeypatch.setattr(
        "backend.skills.sql.skill.get_sql_agent", lambda: FakeAgent())

    state = {
        "question": "按月展示",
        "plan": {"nodes": {"1": {"description": "按月展示"}}},
        "current_step_id": "1",
        "step_results": {},
        "request_context": {
            "session_id": "session-a", "user_id": "3", "tenant_id": "tenant-a",
            "department": "sales", "roles": ["admin"],
        },
        "routing_context": {"sql_query_context": previous.to_dict()},
    }

    asyncio.run(SQLSkill().execute(state, step_capability="sql.query"))

    assert seen["question"] == "按月展示"
    assert seen["query_context"]["question"] == "统计各商品分类的销售额排名"
    # SSE 事件出口被真实透传（而非 None），保证用户端能看到查询进度
    assert callable(seen["event_sink"])
