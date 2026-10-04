"""SQL Agent 列名不匹配时的受控修复回归测试。"""

from backend.sql import sql_agent as agent_module
from backend.sql.sql_result import SQLResult


def test_invalid_order_item_column_is_repaired_to_price(monkeypatch):
    generated = [
        'SELECT oi.unit_price FROM "order".order_items oi LIMIT 5',
        'SELECT oi.price FROM "order".order_items oi LIMIT 5',
    ]
    feedbacks = []
    executions = iter([
        SQLResult.success(
            rows=[{"price": 99}],
            columns=["price"],
            sql=generated[1],
            elapsed=0.01,
        ),
    ])

    monkeypatch.setattr(agent_module, "SQL_AGENT_ENABLED", True)
    monkeypatch.setattr(
        agent_module,
        "select_tables",
        lambda _question, **_kwargs: ["order.order_items"],
    )

    def fake_generate(_question, _tables, feedback=None):
        feedbacks.append(feedback)
        return generated.pop(0)

    monkeypatch.setattr(agent_module, "generate_sql", fake_generate)
    monkeypatch.setattr(
        agent_module,
        "execute_sql_struct",
        lambda *_args, **_kwargs: next(executions),
    )

    result = agent_module.SQLAgent({}, max_retries=1).ask_struct("统计订单明细单价")

    assert result.status == "success"
    assert result.sql_text == 'SELECT oi.price FROM "order".order_items oi LIMIT 5'
    assert len(feedbacks) == 2
    assert feedbacks[1] is not None
    assert "unit_price" in feedbacks[1]
    assert "price" in feedbacks[1]
