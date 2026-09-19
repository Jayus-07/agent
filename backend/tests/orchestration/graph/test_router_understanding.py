"""test_router_understanding.py — router_node 的 CSUnderstanding 接线测试。

口径：CS 预过滤命中后，understanding 结果（实体/缺槽位/规范化文本/
decision_layer）并入 cs_route.metadata，供 CS 子图 experts 直接消费；
纯规则，全 monkeypatch，无 embedding/LLM。
"""
import pytest

from backend.customer_service.router.types import CSRouteResult, CSRoutePath
from backend.orchestration.graph.router_node import router_node


@pytest.fixture()
def cs_forced_env(monkeypatch):
    """CS_ENABLED=true + 域检测恒命中 + CS Router 返回固定动作路由。"""
    import backend.config.customer_service as cs_config
    from backend.customer_service.router import domain_detector
    from backend.customer_service.router import cs_router

    monkeypatch.setattr(cs_config, "CS_ENABLED", True)

    class _FakeDetection:
        is_cs = True
        rule_hits = ["退款"]
        rule_score = 0.9
        vector_score = 0.0
        reason = "rule"

    monkeypatch.setattr(domain_detector, "detect_cached", lambda q: _FakeDetection())

    fixed = CSRouteResult(
        intent="as_refund",
        route_path=CSRoutePath.BUSINESS_ACTION,
        requires_action=True,
        risk_level="high",
        confidence=0.9,
    )

    class _FakeCSTRouter:
        def route(self, q, detection):
            return fixed

    monkeypatch.setattr(cs_router, "get_cs_router", lambda: _FakeCSTRouter())
    return cs_config


class TestRouterUnderstandingWiring:

    def test_entities_into_metadata_zero_rewrite(self, cs_forced_env):
        update = router_node({
            "question": "订单 DEM0-1006 申请退款",
            "domain_hint": "customer_service",
            "session_id": "t-wire-1",
        })
        assert update["route_mode"] == "customer_service"
        # cs_prefilter 契约：cs_route 挂在 cs_context（TypedDict）内
        md = update["cs_context"]["cs_route"]["metadata"]
        # 零改写：形近错别字原样进入 metadata（expert 直接消费）
        assert md["order_id"] == "DEM0-1006"
        assert md["decision_layer"] == "rule"
        assert any(e["type"] == "order_id" and e["value"] == "DEM0-1006"
                   for e in md["entities"])
        assert md["normalized_text"] == "订单 DEM0-1006 申请退款"

    def test_missing_slots_flagged_for_clarify(self, cs_forced_env):
        update = router_node({
            "question": "我要申请退款",
            "domain_hint": "customer_service",
            "session_id": "t-wire-2",
        })
        md = update["cs_context"]["cs_route"]["metadata"]
        assert md["missing_slots"] == ["order_id"]

    def test_no_entities_metadata_still_wired(self, cs_forced_env):
        update = router_node({
            "question": "申请退款 谢谢",
            "domain_hint": "customer_service",
            "session_id": "t-wire-3",
        })
        md = update["cs_context"]["cs_route"]["metadata"]
        assert "order_id" not in md
        assert md["decision_layer"] == "rule"
        assert "entities" in md and md["entities"] == []
