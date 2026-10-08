"""CS LLM 语义理解层单测（2026-10-08 收口改造）。

只 mock 外部边界（LLM 调用 / 订单服务）；断言：
  - validator 白名单与真实 ID 拒绝（P0-02/P0-11/P0-15）
  - 编排 Rule First（规则强命中 LLM=0 次，P0-01）
  - 意图补判采纳/降级（P0-16/P0-17/P0-14）
  - resolver 唯一绑定/多候选/无匹配不猜（P1-01，§十一）
"""
from __future__ import annotations

import pytest

from backend.customer_service.understanding import semantic
from backend.customer_service.understanding.contracts import (
    CSUnderstandingSource,
)
from backend.customer_service.understanding.llm_intent_fallback import (
    rule_intent_is_confident,
)
from backend.customer_service.understanding.validator import (
    validate_confirm_decision,
    validate_intent_candidate,
    validate_severity,
    validate_slots,
    validate_task_plan_candidate,
)
from backend.customer_service.router.intents import INTENT_PROFILES


# ── validator ──────────────────────────────────────────────

class TestValidateIntent:
    def test_whitelist_hit(self):
        assert validate_intent_candidate("t_logistics", frozenset(INTENT_PROFILES)) == (
            "t_logistics", 1.0,
        )

    def test_whitelist_miss_returns_none(self):
        assert validate_intent_candidate("execute_refund_now", frozenset(INTENT_PROFILES)) is None

    def test_non_string_returns_none(self):
        assert validate_intent_candidate({"intent": "t_logistics"}, frozenset(INTENT_PROFILES)) is None

    def test_all_profiles_accepted(self):
        # prompt 动态派生的白名单必须全量可过（单一事实源不漂移）
        for key in INTENT_PROFILES:
            assert validate_intent_candidate(key, frozenset(INTENT_PROFILES)) is not None


class TestValidateSlots:
    def test_accepts_semantic_slots(self):
        report = validate_slots([
            {"name": "product_reference", "value": "耳机", "evidence_text": "那个耳机"},
            {"name": "time_reference", "value": "昨天买的", "confidence": 0.9},
        ])
        assert [s.name for s in report.accepted] == ["product_reference", "time_reference"]
        assert report.accepted[0].confidence == 0.0  # 缺省 0
        assert report.accepted[1].confidence == 0.9
        assert report.rejected_count == 0

    def test_forbidden_real_id_name_rejected(self):
        for name in ("order_id", "refund_id", "ticket_id", "user_id",
                     "tenant_id", "handoff_ticket_id", "confirmation_id"):
            report = validate_slots([{"name": name, "value": "DEMO-1001"}])
            assert not report.accepted
            assert report.rejected_count == 1
            assert report.reject_reasons[0].startswith("forbidden_name")

    def test_real_id_like_value_rejected(self):
        report = validate_slots([
            {"name": "product_reference", "value": "DEMO-1001"},
            {"name": "ordinal", "value": "HANDOFF-ABCD1234"},
            {"name": "attribute", "value": "MO-3C052B3A"},
        ])
        assert not report.accepted
        assert report.rejected_count == 3
        assert all(r.startswith("real_id_like_value") for r in report.reject_reasons)

    def test_plain_chinese_semantic_value_passes(self):
        # 纯中文语义值即便碰巧含「-」也不误杀（日期/数字形态非订单号）
        report = validate_slots([{"name": "time_reference", "value": "10月10日买的"}])
        assert len(report.accepted) == 1

    def test_unknown_name_rejected(self):
        report = validate_slots([{"name": "next_node", "value": "action"}])
        assert not report.accepted
        assert report.reject_reasons[0].startswith("unknown_name")

    def test_duplicate_name_keeps_first(self):
        report = validate_slots([
            {"name": "product_reference", "value": "耳机"},
            {"name": "product_reference", "value": "音箱"},
        ])
        assert len(report.accepted) == 1
        assert report.accepted[0].value == "耳机"
        assert report.rejected_count == 1

    def test_non_list_structure(self):
        report = validate_slots({"slots": []})
        assert not report.accepted
        assert report.fallback_reason == "invalid_structure"

    def test_empty_value_rejected(self):
        report = validate_slots([{"name": "product_reference", "value": "  "}])
        assert not report.accepted
        assert report.fallback_reason == "empty_value"


class TestValidateSeverityAndConfirm:
    def test_severity_whitelist(self):
        assert validate_severity("HIGH") == "high"
        assert validate_severity("critical") is None  # LLM 词表无 critical
        assert validate_severity(3) is None

    def test_confirm_decision_whitelist(self):
        assert validate_confirm_decision("confirm") == "confirm"
        assert validate_confirm_decision("execute") is None
        assert validate_confirm_decision(None) is None

    def test_task_plan_candidate_is_strictly_validated(self):
        candidate = {
            "schema_version": 1, "primary_intent": "t_logistics",
            "tasks": [
                {"task_id": "q1", "capability": "query_logistics", "depends_on": []},
                {"task_id": "a1", "capability": "propose_refund",
                 "depends_on": ["q1"], "condition": {
                     "fact": "shipping_status", "operator": "eq",
                     "value": "not_shipped",
                 }},
            ],
        }
        assert validate_task_plan_candidate(candidate) == candidate
        candidate["tasks"][1]["order_id"] = "MODEL-9999"
        assert validate_task_plan_candidate(candidate) is None


# ── Rule First 判定 ────────────────────────────────────────

class TestRuleFirstGate:
    def test_strong_rule_hit_skips_llm(self):
        assert rule_intent_is_confident("as_refund", 0.8) is True

    def test_unknown_intent_needs_llm(self):
        assert rule_intent_is_confident("unknown", 0.9) is False

    def test_low_confidence_needs_llm(self):
        assert rule_intent_is_confident("t_logistics", 0.3) is False


# ── 编排（semantic.enrich_semantic_understanding）──────────

def _route(intent="unknown", confidence=0.0, **metadata):
    return {"intent": intent, "confidence": confidence, "metadata": dict(metadata)}


class TestSemanticEnrich:
    def test_explicit_conditional_refund_builds_rule_plan_without_llm(self, monkeypatch):
        from backend.config import customer_service as cs_config
        monkeypatch.setattr(cs_config, "CS_UNDERSTANDING_LLM_ENABLED", False)
        route = _route(intent="t_logistics", confidence=0.9)

        result = semantic.enrich_semantic_understanding(
            route, "查下物流，如果没发货就申请退款",
        )

        assert result.task_plan_source == "rule_candidate"
        assert result.task_plan_candidate["tasks"][1]["capability"] == "propose_refund"
        assert route["metadata"]["task_plan_candidate"] == result.task_plan_candidate

    def test_trace_does_not_expose_plan_values(self, monkeypatch):
        from backend.config import customer_service as cs_config
        monkeypatch.setattr(cs_config, "CS_UNDERSTANDING_LLM_ENABLED", False)
        route = _route(intent="t_logistics", confidence=0.9)
        result = semantic.enrich_semantic_understanding(
            route, "查下物流，如果没发货就申请退款",
        )

        fields = result.to_trace_fields()
        assert fields["cs_task_plan_source"] == "rule_candidate"
        assert fields["cs_task_count"] == 2
        assert "task_plan_candidate" not in fields
        assert "propose_refund" not in str(fields)

    def test_disabled_switch_untouched(self, monkeypatch):
        from backend.config import customer_service as cs_config
        monkeypatch.setattr(cs_config, "CS_UNDERSTANDING_LLM_ENABLED", False)
        called = []
        monkeypatch.setattr(
            "backend.customer_service.understanding.semantic.llm_intent_candidate",
            lambda *_: called.append(1) or ("t_logistics", 0.9),
        )
        route = _route()
        result = semantic.enrich_semantic_understanding(route, "帮我查下物流")
        assert not called
        assert route["intent"] == "unknown"
        assert result.source is CSUnderstandingSource.UNCHANGED

    def test_strong_rule_hit_zero_llm(self, monkeypatch):
        """P0-01：规则强命中 → 意图 LLM = 0 次。"""
        called = []
        monkeypatch.setattr(
            "backend.customer_service.understanding.semantic.llm_intent_candidate",
            lambda *_: called.append(1) or ("t_logistics", 0.9),
        )
        route = _route(intent="as_refund", confidence=0.8)
        result = semantic.enrich_semantic_understanding(route, "我要退款")
        assert not called  # LLM 0 次
        assert route["intent"] == "as_refund"
        assert result.source is CSUnderstandingSource.RULE

    def test_low_confidence_intent_adopted_with_profile_derivation(self, monkeypatch):
        from backend.config import customer_service as cs_config
        monkeypatch.setattr(cs_config, "CS_UNDERSTANDING_LLM_ENABLED", True)
        monkeypatch.setattr(cs_config, "CS_SLOT_LLM_ENABLED", False)
        monkeypatch.setattr(
            "backend.customer_service.understanding.semantic.llm_intent_candidate",
            lambda *_: ("t_logistics", 0.88),
        )
        route = _route(intent="unknown", confidence=0.2)
        result = semantic.enrich_semantic_understanding(route, "耳机咋还没到")
        assert route["intent"] == "t_logistics"
        # 路由置信度取固定保守值，不采信 LLM 自报分数
        assert route["confidence"] == cs_config.CS_LLM_INTENT_CONFIDENCE
        assert route["requires_auth"] is True  # INTENT_PROFILES 画像派生
        assert route["route_path"] == "business_query"
        assert route["metadata"]["intent_source"] == "llm_fallback"
        assert result.source is CSUnderstandingSource.LLM_FALLBACK

    def test_intent_llm_failure_soft_degrade(self, monkeypatch):
        """P0-16/P0-17：LLM 失败/非法输出 → 规则原样，业务不断。"""
        from backend.config import customer_service as cs_config
        monkeypatch.setattr(cs_config, "CS_UNDERSTANDING_LLM_ENABLED", True)
        monkeypatch.setattr(cs_config, "CS_SLOT_LLM_ENABLED", False)
        monkeypatch.setattr(
            "backend.customer_service.understanding.semantic.llm_intent_candidate",
            lambda *_: None,
        )
        route = _route(intent="unknown", confidence=0.2)
        result = semantic.enrich_semantic_understanding(route, "那段话看不懂")
        assert route["intent"] == "unknown"
        assert result.source is CSUnderstandingSource.RULE

    def test_slot_candidates_written_to_metadata(self, monkeypatch):
        """P0 golden #2：自然物流问题 → 语义槽位候选（无真实 ID）。"""
        from backend.config import customer_service as cs_config
        from backend.customer_service.understanding.contracts import CSSlotCandidate

        monkeypatch.setattr(cs_config, "CS_UNDERSTANDING_LLM_ENABLED", True)
        monkeypatch.setattr(cs_config, "CS_SLOT_LLM_ENABLED", True)
        monkeypatch.setattr(
            "backend.customer_service.understanding.semantic.llm_intent_candidate",
            lambda *_: ("t_logistics", 0.88),
        )
        monkeypatch.setattr(
            "backend.customer_service.understanding.semantic.llm_slot_candidates",
            lambda *_a, **_k: (
                [CSSlotCandidate(name="product_reference", value="耳机"),
                 CSSlotCandidate(name="time_reference", value="昨天买的")],
                2, 0, "",
            ),
        )
        route = _route(intent="unknown", confidence=0.2)
        semantic.enrich_semantic_understanding(route, "我昨天买的那个耳机怎么还没到？")
        slots = route["metadata"]["semantic_slots"]
        assert {s["name"] for s in slots} == {"product_reference", "time_reference"}
        assert all("order_id" not in s["name"] for s in slots)

    def test_explicit_order_entity_skips_slot_llm(self, monkeypatch):
        """有显式订单号 = Rule First，槽位 LLM 0 次。"""
        from backend.config import customer_service as cs_config

        monkeypatch.setattr(cs_config, "CS_UNDERSTANDING_LLM_ENABLED", True)
        monkeypatch.setattr(cs_config, "CS_SLOT_LLM_ENABLED", True)
        monkeypatch.setattr(
            "backend.customer_service.understanding.semantic.llm_slot_candidates",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("slot LLM must not run")),
        )
        route = _route(
            intent="t_logistics", confidence=0.8,
            order_id="DEMO-1001", entities=[{"type": "order_id", "value": "DEMO-1001"}],
        )
        semantic.enrich_semantic_understanding(route, "查 DEMO-1001 到哪了")
        assert "semantic_slots" not in route["metadata"]


# ── resolver（真实 ID 只来自业务数据）──────────────────────

class _FakeOrderService:
    def __init__(self, orders):
        self._orders = orders

    def list_recent_orders_with_products(self, user_id, limit=20):
        return self._orders


def _order(order_no, product, created_days_ago=1):
    """created_days_ago 相对 UTC 今天，避免硬编码日期跨日失效。"""
    from datetime import datetime, timedelta, timezone

    ts = datetime.now(timezone.utc) - timedelta(days=created_days_ago)
    return {"order_no": order_no, "product_names": product,
            "created_at": ts.isoformat(), "status": "shipped",
            "total_amount": 199.0}


class TestSemanticSlotResolver:
    def _patch_orders(self, monkeypatch, orders):
        monkeypatch.setattr(
            "backend.customer_service.service.order_service.get_order_service",
            lambda: _FakeOrderService(orders),
        )

    def test_unique_match_binds(self, monkeypatch):
        from backend.customer_service.context.semantic_slots import (
            resolve_semantic_slots,
        )

        self._patch_orders(monkeypatch, [_order("DEMO-1001", "无线耳机")])
        r = resolve_semantic_slots(
            "default", "u1",
            [{"name": "product_reference", "value": "耳机"}],
        )
        assert r.resolved and r.order_id == "DEMO-1001"

    def test_ambiguous_returns_candidates_not_guess(self, monkeypatch):
        """P0 golden #4：两个耳机订单 → 追问候选，绝不自动选最近。"""
        from backend.customer_service.context.semantic_slots import (
            resolve_semantic_slots,
        )

        self._patch_orders(monkeypatch, [
            _order("DEMO-1001", "无线耳机", created_days_ago=1),
            _order("DEMO-1002", "蓝牙耳机", created_days_ago=6),
        ])
        r = resolve_semantic_slots(
            "default", "u1",
            [{"name": "product_reference", "value": "耳机"}],
        )
        assert not r.resolved
        assert r.ambiguous and len(r.candidates) == 2
        assert {c["order_no"] for c in r.candidates} == {"DEMO-1001", "DEMO-1002"}

    def test_time_reference_narrows(self, monkeypatch):
        from backend.customer_service.context.semantic_slots import (
            resolve_semantic_slots,
        )

        self._patch_orders(monkeypatch, [
            _order("DEMO-1001", "无线耳机", created_days_ago=1),
            _order("DEMO-1002", "蓝牙耳机", created_days_ago=36),
        ])
        r = resolve_semantic_slots(
            "default", "u1",
            [{"name": "product_reference", "value": "耳机"},
             {"name": "time_reference", "value": "昨天买的"}],
        )
        # 时间窗口把 9 月那单过滤掉 → 唯一绑定
        assert r.resolved and r.order_id == "DEMO-1001"

    def test_no_match_never_guesses(self, monkeypatch):
        from backend.customer_service.context.semantic_slots import (
            resolve_semantic_slots,
        )

        self._patch_orders(monkeypatch, [_order("DEMO-1001", "手机壳")])
        r = resolve_semantic_slots(
            "default", "u1",
            [{"name": "product_reference", "value": "耳机"}],
        )
        assert not r.resolved and not r.ambiguous and not r.candidates

    def test_db_error_propagates(self, monkeypatch):
        """服务故障必须上抛（STOP C：不吞成「没有匹配」）。"""
        from backend.customer_service.errors import DatabaseError
        from backend.customer_service.context.semantic_slots import (
            resolve_semantic_slots,
        )

        def _boom(*_a, **_k):
            raise DatabaseError("gateway down")

        monkeypatch.setattr(
            "backend.customer_service.service.order_service.get_order_service",
            lambda: type("S", (), {"list_recent_orders_with_products": staticmethod(_boom)})(),
        )
        with pytest.raises(DatabaseError):
            resolve_semantic_slots(
                "default", "u1",
                [{"name": "product_reference", "value": "耳机"}],
            )

    def test_no_product_reference_no_query(self, monkeypatch):
        from backend.customer_service.context.semantic_slots import (
            resolve_semantic_slots,
        )

        monkeypatch.setattr(
            "backend.customer_service.service.order_service.get_order_service",
            lambda: (_ for _ in ()).throw(AssertionError("must not query")),
        )
        r = resolve_semantic_slots("default", "u1", [{"name": "ordinal", "value": "2"}])
        assert not r.resolved
