"""test_context_manager.py — Context Manager 订单槽位解析（迁移 B4）

收敛前 query/action 各持一份「注入 > 当前轮实体」优先级拼接与实体抽取
包装循环；本文件锁定 context_manager 单一实现的契约。实体抽取的完整
行为（多段连字不截断/关键词纯数字/形近错别字零改写）由
test_order_ref_resolution.py 经专家侧委托路径继续覆盖。
"""
from __future__ import annotations

from backend.customer_service.context_manager import (
    extract_order_entity,
    resolve_order_slot,
)


class TestExtractOrderEntity:

    def test_multi_segment_not_truncated(self):
        assert extract_order_entity("查一下订单 ORD-20260915-0042 的状态") == (
            "ORD-20260915-0042"
        )

    def test_no_entity_returns_empty(self):
        assert extract_order_entity("我要申请退款") == ""

    def test_empty_input(self):
        assert extract_order_entity("") == ""


class TestResolveOrderSlot:
    """优先级契约：预过滤注入（本轮权威 referent）> 当前轮显式实体。"""

    def test_injected_wins_over_message_entity(self):
        cs_route = {"metadata": {"order_id": "DEMO-1001"}}
        assert resolve_order_slot(cs_route, "订单 ORD-20260915-0042 退款") == (
            "DEMO-1001"
        )

    def test_message_entity_when_no_injection(self):
        assert resolve_order_slot({}, "订单 ORD-20260915-0042 申请退款") == (
            "ORD-20260915-0042"
        )

    def test_blank_injection_treated_as_absent(self):
        assert resolve_order_slot(
            {"metadata": {"order_id": "   "}}, "DEMO-1002 到哪了"
        ) == "DEMO-1002"

    def test_empty_metadata_dict(self):
        assert resolve_order_slot({"metadata": {}}, "DEMO-1002 到哪了") == "DEMO-1002"

    def test_none_route(self):
        assert resolve_order_slot(None, "我要退款") == ""

    def test_both_absent_returns_empty_string(self):
        # 缺槽语义归消费方（query 降级列表 / action 结构化追问），此处只保证空串
        assert resolve_order_slot({}, "帮我退款") == ""
