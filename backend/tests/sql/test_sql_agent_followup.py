"""SQL Agent 将会话上下文用于追问改写的测试。"""

from backend.sql import sql_agent as agent_module
from backend.sql.query_context import SQLQueryContext, permission_fingerprint
from backend.sql.sql_result import SQLResult
from backend.tests.sql.conftest import build_ctx


def test_agent_generates_from_standalone_followup_question(monkeypatch):
    policy = build_ctx(user_id="3", department="sales", roles=("admin",))
    previous = SQLQueryContext(
        question="统计各商品分类的销售额排名",
        standalone_question="统计各商品分类的销售额排名",
        tables=("product.categories", "product.products", "order.order_items"),
        permission_fingerprint=permission_fingerprint(policy),
    )
    seen = {}

    monkeypatch.setattr(agent_module, "SQL_AGENT_ENABLED", True)
    def fake_select_tables(question):
        seen["selected_question"] = question
        return ["product.products"]

    monkeypatch.setattr(agent_module, "select_tables", fake_select_tables)

    def fake_generate_sql(question, _tables, feedback=None):
        seen["generated_question"] = question
        return "SELECT product_name FROM product.products LIMIT 5"

    monkeypatch.setattr(agent_module, "generate_sql", fake_generate_sql)
    monkeypatch.setattr(
        agent_module,
        "execute_sql_struct",
        lambda *_args, **_kwargs: SQLResult.success(
            rows=[{"product_name": "A"}],
            columns=["product_name"],
            sql="SELECT product_name FROM product.products LIMIT 5",
        ),
    )

    result = agent_module.SQLAgent({}, max_retries=0).ask_struct(
        "按月展示", policy=policy, query_context=previous.to_dict()
    )

    assert result.status == "success"
    assert "统计各商品分类的销售额排名" in seen["selected_question"]
    assert "按月展示" in seen["generated_question"]
