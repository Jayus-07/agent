"""角色数据范围与 SQL 表目录一致性测试。"""

from backend.tests.sql.conftest import build_ctx
from backend.sql.policy import SQLPolicyGuard


def test_editor_only_sees_tables_expressible_by_department_scope():
    visible = set(SQLPolicyGuard().get_allowed_tables(
        build_ctx(user_id="3", department="sales", roles=("editor",))
    ))

    assert "product.products" in visible
    assert "inventory.inventory" in visible
    assert "finance.expenses" not in visible
    assert "order.order_items" not in visible


def test_admin_sees_internal_and_personal_tables():
    visible = set(SQLPolicyGuard().get_allowed_tables(
        build_ctx(user_id="3", department="sales", roles=("admin",))
    ))

    assert "finance.expenses" in visible
    assert "order.order_items" in visible
