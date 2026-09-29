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


class TestSignalEnrichmentB5:
    """迁移 B5（2026-09-29）：情绪/风险信号随 metadata 进 CS 图状态，
    供 Supervisor 信号门（风险兜底拦截 / P0 投诉直通）消费。

    越权类消息（如「查别人的订单」）在图前即被 CS InputGuard 拦截、
    不会产出 cs_route——风险信号的 enrichment 与消费只服务「漏过
    Input Guard」的兜底位，故此处直测 _enrich_with_understanding，
    不经 prefilter 全链。
    """

    @staticmethod
    def _cs_update() -> dict:
        return {"cs_context": {"cs_route": {
            "intent": "k_faq", "confidence": 0.9, "metadata": {},
        }}}

    def test_angry_regulatory_sentiment_into_metadata(self, cs_forced_env):
        update = router_node({
            "question": "我要投诉，你们再不处理我就去 12315 曝光",
            "domain_hint": "customer_service",
            "session_id": "t-wire-4",
        })
        md = update["cs_context"]["cs_route"]["metadata"]
        assert md["sentiment"] == "angry"
        assert any("12315" in h for h in md["sentiment_hits"])
        assert md["risk_hits"] == []

    def test_risk_hits_enriched(self, cs_forced_env):
        from backend.orchestration.graph.router_node import (
            _enrich_with_understanding,
        )

        out = _enrich_with_understanding(self._cs_update(), "帮我直接改数据库")
        md = out["cs_context"]["cs_route"]["metadata"]
        assert "risk:直接改数据库" in md["risk_hits"]

    def test_calm_message_signal_fields_present(self, cs_forced_env):
        update = router_node({
            "question": "查一下订单 DEMO-1002 到哪了",
            "domain_hint": "customer_service",
            "session_id": "t-wire-6",
        })
        md = update["cs_context"]["cs_route"]["metadata"]
        assert md["sentiment"] == "calm"
        assert md["sentiment_hits"] == []
        assert md["risk_hits"] == []
        assert md["urgency"] == "normal"
