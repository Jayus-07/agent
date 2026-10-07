"""test_cs_failure_semantics.py — STOP C：Customer Service 错误分类回归

2026-10-07 全项目 Tool Failure Semantics 收口（任务 §七/§二十一）：
- Provider Down ≠ Not Found：订单/物流服务故障必须答「服务暂时不可用」，
  不得译成「没有找到订单」「暂无物流信息」这类业务假信息；
- 工单列表 DB 故障不得译成「您当前没有进行中的工单」；
- 写操作查重失败必须 fail-closed（拒绝资格检查），不得放行。

只 mock 外部边界（service 层注入异常），expert 分类逻辑走真实代码。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch as mp

import pytest

from backend.customer_service.errors import DatabaseError, OrderNotFoundError
from backend.customer_service.experts.query import execute_query


def _ask(message: str, route: dict, state: dict | None = None) -> str:
    """execute_query → 用户可见话术（ExpertResult.response_draft）"""
    result = execute_query(message, route, state or _state())
    draft = result["response_draft"] if isinstance(result, dict) else result.response_draft
    return draft


def _state() -> dict:
    return {"user_id": "u-1", "tenant_id": "default", "session_id": "s-1"}


def _order_route() -> dict:
    # 缺陷9：cs_route.metadata 注入的订单号优先（回指承接权威 referent）
    return {"intent": "t_order_status", "metadata": {"order_id": "DEMO-1001"}}


def _logistics_route() -> dict:
    return {"intent": "t_logistics", "metadata": {"order_id": "DEMO-1001"}}


class _FakeOrderService:
    def __init__(self, exc: Exception | None = None, orders: list | None = None):
        self._exc = exc
        self._orders = orders or []

    def query_orders(self, user_id: str, order_id: str | None = None,
                     query_type: str = "list"):
        if self._exc is not None:
            raise self._exc
        return SimpleNamespace(orders=self._orders, total_count=len(self._orders),
                               query_type=query_type)


class TestOrderFailureClassification:

    def test_cs_order_not_found_vs_provider_down(self):
        """C1：真不存在 → 「没有找到」；服务故障 → 「不可用」——两种话术必须区分"""
        with mp("backend.customer_service.service.order_service.get_order_service",
                lambda: _FakeOrderService(OrderNotFoundError("not found for user"))):
            reply = _ask("DEMO-1001 到哪了", _order_route(), _state())
        assert "没有找到订单 DEMO-1001" in reply

        with mp("backend.customer_service.service.order_service.get_order_service",
                lambda: _FakeOrderService(DatabaseError("业务网关不可用: 500"))):
            reply_down = _ask("DEMO-1001 到哪了", _order_route(), _state())
        assert "暂时不可用" in reply_down
        assert "没有找到" not in reply_down, "服务故障不得译成订单不存在"

    def test_cs_gateway_timeout_not_order_not_found(self):
        """C2/C3：网关超时/5xx（DatabaseError）→ unavailable 话术，绝不绝不绝不
        允许出现「订单不存在」语义"""
        for err in (
            DatabaseError("业务网关不可用: ConnectTimeout"),
            DatabaseError("业务网关返回失败 code=500"),
            RuntimeError("未分类意外故障"),  # unknown → 安全失败，也不得假信息
        ):
            with mp("backend.customer_service.service.order_service.get_order_service",
                    lambda e=err: _FakeOrderService(e)):
                reply = _ask("DEMO-1001 到哪了", _order_route(), _state())
            assert "暂时不可用" in reply, f"{err!r} 必须译成服务不可用"
            assert "没有找到" not in reply and "不存在" not in reply

    def test_cs_order_list_down_not_empty_list(self):
        """列表查询故障 → 「不可用」，不得渲染成「您当前没有相关订单记录」"""
        with mp("backend.customer_service.service.order_service.get_order_service",
                lambda: _FakeOrderService(DatabaseError("查询订单列表失败"))):
            reply = _ask("查我的所有订单", {"intent": "t_order_status"}, _state())
        assert "暂时不可用" in reply
        assert "没有相关订单" not in reply


class _FakeLogisticsService:
    def __init__(self, exc: Exception | None = None):
        self._exc = exc

    def query_logistics(self, user_id: str, order_id: str):
        if self._exc is not None:
            raise self._exc
        return SimpleNamespace(order_no=order_id, status_display="运输中",
                               tracking_info="")


class TestLogisticsFailureClassification:

    def test_cs_logistics_unavailable_not_no_data(self):
        """C5：物流服务不可用 → 「物流服务暂时不可用」，不得伪造「暂无物流」"""
        with mp("backend.customer_service.service.logistics_service.get_logistics_service",
                lambda: _FakeLogisticsService(DatabaseError("查询物流信息失败"))):
            reply = _ask("我的物流到哪了", _logistics_route(), _state())
        assert "物流服务暂时不可用" in reply
        assert "暂无物流" not in reply

    def test_cs_logistics_not_found_vs_down(self):
        """订单无物流记录（OrderNotFoundError）与服务故障（DatabaseError）区分"""
        with mp("backend.customer_service.service.logistics_service.get_logistics_service",
                lambda: _FakeLogisticsService(OrderNotFoundError("no logistics"))):
            reply = _ask("我的物流到哪了", _logistics_route(), _state())
        assert "没有找到订单" in reply

    def test_cs_logistics_prefetch_down_not_no_data(self):
        """C-P0-2：前置取单（最新订单）失败 → 「不可用」，不得落到「暂无物流信息」"""
        route = {"intent": "t_logistics"}  # 无注入订单 → 走 _get_latest_order_id
        with mp("backend.customer_service.service.order_service.get_order_service",
                lambda: _FakeOrderService(DatabaseError("业务网关不可用"))):
            reply = _ask("我的物流到哪了", route, _state())
        assert "暂时不可用" in reply
        assert "暂无物流" not in reply and "请先查询您的订单" not in reply

    def test_cs_logistics_no_order_honest_empty(self):
        """真没有订单（空列表）→ 如实说明无单可查（合法空态，非故障）"""
        route = {"intent": "t_logistics"}
        with mp("backend.customer_service.service.order_service.get_order_service",
                lambda: _FakeOrderService(orders=[])):
            reply = _ask("我的物流到哪了", route, _state())
        assert "没有可查询物流的订单" in reply


class TestTicketFailureClassification:

    def test_cs_ticket_list_down_not_empty(self):
        """C-P1-3：工单 DB 故障 → 「暂时查不到…请稍后再试」，不得译成「没有工单」"""
        class _DownStore:
            def list_for_user_sync(self, user_id, limit=10):
                raise DatabaseError("DB down")

        with mp("backend.customer_service.ticket_store.get_ticket_store",
                lambda: _DownStore()):
            reply = _ask("我的工单进度", {"intent": "t_ticket_status"}, _state())
        assert "暂时查不到" in reply and "稍后再试" in reply
        assert "没有进行中的工单" not in reply

    def test_cs_ticket_empty_list_is_legitimate(self):
        """空列表（真没有工单）→ 「没有进行中的工单」（合法空态保留）"""
        class _EmptyStore:
            def list_for_user_sync(self, user_id, limit=10):
                return []

        with mp("backend.customer_service.ticket_store.get_ticket_store",
                lambda: _EmptyStore()):
            reply = _ask("我的工单进度", {"intent": "t_ticket_status"}, _state())
        assert "没有进行中的工单" in reply


class TestWriteFailClosed:

    def test_cs_write_failure_fail_closed(self):
        """C6：退款查重失败必须 fail-closed 拒绝资格检查（不许放行重复退款）"""
        from backend.customer_service.service.refund_service import RefundService
        from backend.customer_service.errors import DatabaseError as DE

        class _ErrResult:
            status = "error"
            error = "db unreachable"
            rows: list = []

        svc = RefundService()
        with mp("backend.customer_service.service.order_service._use_http_gateway",
                lambda: False), \
             mp("backend.sql.executor.execute_sql_struct",
                lambda sql, params=None: _ErrResult()):
            with pytest.raises(DE) as ei:
                svc._has_existing_refund("order-pk-1")
        assert "拒绝" in str(ei.value) or "无法核实" in str(ei.value)
