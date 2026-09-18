"""test_order_ref_resolution.py — P0 实测缺陷回归（2026-09-19）

覆盖 E2E 基线（五剧本 ×10）发现的两处缺陷：

1. 动作提案缺订单槽位时 action.py 回退 "latest"，refund/after_sales 的
   ``_get_order`` 此前按字面匹配 → 必然 ``OrderNotFoundError``，退款/退货
   确认卡永远无法生成（C 剧本实测 10/10 报错）。
2. 订单号抽取正则 ``[A-Za-z]{2,10}-\\d{2,12}`` 对两段式订单号截断
   （ORD-20260915-0042 → ORD-20260915），违反「订单号不得改写」要求。
"""
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from backend.customer_service.errors import OrderNotFoundError
from backend.customer_service.experts.action import _extract_order_id_from_message
from backend.customer_service.experts.query import _extract_order_no
from backend.customer_service.service.after_sales_service import AfterSalesService
from backend.customer_service.service.refund_service import RefundService

# 与 test_order_service.py 等一致：只 mock 外部边界（SQL 执行器）
_EXEC_PATCH = "backend.sql.executor.execute_sql_struct"


@dataclass
class FakeSQLResult:
    rows: list
    status: str = "success"
    error: str = ""
    row_count: int = 0


def _order_row(order_id="42", order_no="DEMO-1006", created_at="2026-09-16"):
    return {
        "id": order_id,
        "order_no": order_no,
        "customer_id": "99001",
        "total_amount": 358.0,
        "status": "pending",
        "payment_status": "unpaid",
        "created_at": created_at,
    }


class TestLatestOrderResolution:
    """_get_order("latest") 语义化兜底：两个动作服务行为必须一致。"""

    @pytest.mark.parametrize("svc_cls", [RefundService, AfterSalesService])
    def test_latest_returns_newest_row(self, svc_cls):
        newest = _order_row(order_no="DEMO-1006", created_at="2026-09-16")
        captured = {}

        def fake_exec(sql, params=None, **kwargs):
            captured["sql"] = sql
            captured["params"] = params
            return FakeSQLResult(rows=[newest])

        with patch(_EXEC_PATCH, side_effect=fake_exec):
            row = svc_cls()._get_order("u1", "latest")

        assert row["order_no"] == "DEMO-1006"
        assert "ORDER BY created_at DESC" in captured["sql"]
        assert "LIMIT 1" in captured["sql"]
        assert captured["params"]["user_id"] == "u1"
        # 兜底查询不得带 order_id 过滤（否则复现字面匹配缺陷）
        assert "%(order_id)s" not in captured["sql"]

    @pytest.mark.parametrize("svc_cls", [RefundService, AfterSalesService])
    def test_latest_without_orders_raises(self, svc_cls):
        with patch(_EXEC_PATCH, return_value=FakeSQLResult(rows=[], status="no_data")):
            with pytest.raises(OrderNotFoundError):
                svc_cls()._get_order("u1", "latest")

    @pytest.mark.parametrize("svc_cls", [RefundService, AfterSalesService])
    def test_literal_id_path_unchanged(self, svc_cls):
        """字面订单号路径必须保持原语义（id/order_no 精确匹配 + 属主过滤）。"""
        captured = {}

        def fake_exec(sql, params=None, **kwargs):
            captured["sql"] = sql
            captured["params"] = params
            return FakeSQLResult(rows=[_order_row(order_no="DEMO-1002")])

        with patch(_EXEC_PATCH, side_effect=fake_exec):
            row = svc_cls()._get_order("u1", "DEMO-1002")

        assert row["order_no"] == "DEMO-1002"
        assert "id::text = %(order_id)s" in captured["sql"]
        assert captured["params"]["order_id"] == "DEMO-1002"
        assert "ORDER BY" not in captured["sql"]


class TestOrderIdExtraction:
    """订单号抽取：多段连字不得截断（规划稿 P1「订单号 0 改写」）。"""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("查一下订单 ORD-20260915-0042 的状态", "ORD-20260915-0042"),
            ("订单 ORD-20260915-0042 申请退款", "ORD-20260915-0042"),
            ("DEMO-1002 到哪了", "DEMO-1002"),
            ("帮我查 ord-07 流水", "ORD-07"),
        ],
    )
    def test_action_extraction_full_match(self, text, expected):
        assert _extract_order_id_from_message(text) == expected

    def test_action_digits_after_keyword(self):
        assert _extract_order_id_from_message("订单号 123456789") == "123456789"

    def test_action_fallback_latest(self):
        assert _extract_order_id_from_message("我要申请退款") == "latest"
        assert _extract_order_id_from_message("") == "latest"

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("查一下订单 ORD-20260915-0042 的状态", "ORD-20260915-0042"),
            ("DEMO-1002 到哪了", "DEMO-1002"),
        ],
    )
    def test_query_extraction_full_match(self, text, expected):
        assert _extract_order_no(text) == expected

    def test_query_no_order_returns_none(self):
        assert _extract_order_no("我的订单什么时候发货") is None


class TestProposalDeclineReadable:
    """资格拒绝/无可用订单必须转成可读答复，而不是专家异常→通用报错。"""

    @staticmethod
    def _run(exc: Exception) -> dict:
        from unittest.mock import patch as _patch

        from backend.customer_service.experts import action as action_mod

        state = {"session_id": "s1", "user_id": "u1"}
        with _patch(
            "backend.customer_service.security.permission."
            "PermissionChecker.validate_user_identity", return_value="u1",
        ), _patch(
            "backend.customer_service.confirmation_store.get_confirmation_store",
        ) as mock_store, _patch.object(
            action_mod, "_build_new_proposal", side_effect=exc,
        ):
            mock_store.return_value.load.return_value = None
            return action_mod.execute_action(
                "我要申请退款", {"intent": "as_refund"}, state,
            )

    def test_not_eligible_becomes_readable_answer(self):
        from backend.customer_service.errors import OrderNotEligibleError

        result = self._run(OrderNotEligibleError(
            "订单状态 'pending' 不允许退款，仅支持: completed, paid, shipped"))
        assert result["status"] == "success"
        assert "pending" in result["response_draft"]
        assert "订单号" in result["response_draft"]

    def test_order_not_found_becomes_readable_answer(self):
        result = self._run(OrderNotFoundError("Order latest not found for user 99001"))
        assert result["status"] == "success"
        assert "订单" in result["response_draft"]
        assert "处理您的请求时遇到了问题" not in result["response_draft"]
