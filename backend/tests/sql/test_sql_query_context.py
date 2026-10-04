"""SQL 查询会话记忆与追问解析测试。"""

from backend.orchestration.context.context_repository import (
    ContextMutation,
    MemoryConversationContextRepository,
    MutationType,
)
from backend.sql.query_context import (
    SQLQueryContext,
    is_sql_context_compatible,
    resolve_sql_followup,
)


def test_followup_uses_previous_query_without_storing_rows():
    previous = SQLQueryContext(
        question="统计各商品分类的销售额排名",
        standalone_question="统计各商品分类的销售额排名",
        tables=("product.categories", "product.products", "order.order_items"),
        columns=("category_name", "total_sales"),
        sql='SELECT c.name, SUM(oi.quantity * oi.price) FROM "order".order_items oi',
        permission_fingerprint="fp-editor",
    )

    resolved = resolve_sql_followup("按月展示", previous)

    assert resolved["follow_up_detected"] is True
    assert "统计各商品分类的销售额排名" in resolved["standalone_question"]
    assert "按月展示" in resolved["standalone_question"]
    assert "rows" not in previous.to_dict()


def test_sql_context_isolated_by_tenant_and_user():
    repo = MemoryConversationContextRepository()
    context = SQLQueryContext(
        question="查库存",
        standalone_question="查库存",
        tables=("inventory.inventory",),
        permission_fingerprint="fp-editor",
    )

    result = repo.mutate(
        "tenant-a", "user-a", "session-a",
        ContextMutation(MutationType.SET_SQL_QUERY_CONTEXT, {
            "context": context.to_dict(),
        }),
    )

    assert result.status == "applied"
    assert repo.peek("tenant-a", "user-a", "session-a").sql_query_context["question"] == "查库存"
    assert repo.peek("tenant-a", "user-b", "session-a") is None
    assert repo.peek("tenant-b", "user-a", "session-a") is None


def test_permission_fingerprint_change_invalidates_context():
    context = SQLQueryContext(
        question="查库存",
        standalone_question="查库存",
        permission_fingerprint="fp-editor",
    )

    assert is_sql_context_compatible(context, "fp-editor") is True
    assert is_sql_context_compatible(context, "fp-viewer") is False
