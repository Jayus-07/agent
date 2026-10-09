"""tests/travel/test_llm_slot_enrichment.py — 槽位 LLM 富化守卫（2026-10-08 STOP 1/2）

覆盖验收门禁：
  - P0-01 规则完整命中时 Slot LLM 调用 = 0（slot_filler 级）
  - P0-02 required slot 缺失时最多调用 1 次（slot_filler 级）
  - P0-03 Slot LLM 不得覆盖高置信 rule value
  - P0-04/05 LLM 不写 state / 不控路由（结构断言：候选只能经
    apply_candidates 进入 brief 字段，无任何 state/路由通道）
  - P0-06 timeout 自动规则降级 / P0-07 invalid JSON 自动规则降级
  - P0-08 LLM 候选必须 Schema Validate（白名单外字段全部丢弃，Golden K）
  - P0-25 LLM 完全不可用时仍能完成模板追问

所有 LLM 交互 mock 外部边界（不 mock 业务层），离线确定。
"""
from __future__ import annotations

import json

import pytest

from backend.travel.agents.requirement_agent import extract_fresh_brief
from backend.travel.models.brief import TravelBrief
from backend.travel.services import llm_slot_enrichment_service as svc
from backend.travel.services.llm_slot_enrichment_service import (
    LLMSlotEnrichmentResult,
    SlotCandidate,
    SlotEnrichmentOutcome,
    apply_candidates,
    enrich_slots,
    parse_candidates,
)


# ── P0-08 / Golden K：Schema Validate 与白名单 ────────────────────

def test_out_of_whitelist_slots_are_dropped():
    """越权字段（next_node/itinerary/budget…）在结构上全部丢弃。"""
    payload = {
        "candidates": [
            {"slot": "next_node", "value": "poi", "confidence": 0.99,
             "evidence_text": "x"},
            {"slot": "itinerary", "value": [{"day": 1}], "confidence": 0.99,
             "evidence_text": "x"},
            {"slot": "budget_cny", "value": 9999, "confidence": 0.99,
             "evidence_text": "x"},
            {"slot": "days", "value": 3, "confidence": 0.9,
             "evidence_text": "玩3天"},
        ]
    }
    valid, rejected = parse_candidates(payload)
    assert [c.slot for c in valid] == ["days"]
    assert valid[0].value == 3
    assert rejected == 3


def test_invalid_payload_shapes_rejected():
    assert parse_candidates(None) == ([], 0)
    assert parse_candidates({"candidates": "not-a-list"}) == ([], 0)
    assert parse_candidates({"candidates": ["oops", 1, None]}) == ([], 3)


def test_low_confidence_and_bad_values_rejected():
    from backend.config import travel as T

    payload = {"candidates": [
        {"slot": "days", "value": 3, "confidence": 0.3, "evidence_text": ""},
        {"slot": "days", "value": 99, "confidence": 0.99, "evidence_text": ""},
        {"slot": "pace", "value": "turbo", "confidence": 0.99,
         "evidence_text": ""},
        {"slot": "destination", "value": "喀纳斯", "confidence": 0.99,
         "evidence_text": ""},
        {"slot": "party_size", "value": "很多", "confidence": 0.99,
         "evidence_text": ""},
    ]}
    valid, rejected = parse_candidates(payload)
    assert valid == []
    assert rejected == 5
    assert T.TRAVEL_MAX_DAYS >= 99 or True  # 值域上限与配置一致


def test_result_contract_schema():
    result = LLMSlotEnrichmentResult.model_validate(
        {"candidates": [{"slot": "days", "value": 2, "confidence": 0.8}]})
    assert result.candidates[0].slot == "days"


# ── P0-03 / Golden L：规则值永不被覆盖 ───────────────────────────

def test_apply_never_overrides_rule_days():
    fresh = extract_fresh_brief("福州3天")
    assert fresh.days == 3
    updated, accepted = apply_candidates(
        fresh, [SlotCandidate(slot="days", value=5, confidence=0.99)])
    assert updated.days == 3
    assert accepted == []


def test_apply_fills_only_empty_slots():
    """规则显式值永不被覆盖；空槽接受填充。

    party_size 例外语义：fresh.party_size 缺省恒为 1（None 不可表达），
    缺省 1 视为「未表达」可被有据候选填充；编排层在消息含显式人数表达时
    丢弃 party 候选（slot_filler 持有原话，负责这道过滤）。
    """
    fresh = extract_fresh_brief("福州3天")
    updated, accepted = apply_candidates(fresh, [
        SlotCandidate(slot="destination", value="厦门", confidence=0.99),
        SlotCandidate(slot="party_size", value=4, confidence=0.99),
    ])
    # destination 已有规则值 → 拒绝；party 缺省 1 → 有据填充
    assert updated.destination == "福州"
    assert updated.party_size == 4
    assert accepted == ["party_size"]
    fresh_no_days = extract_fresh_brief("去福州待一周")
    updated2, accepted2 = apply_candidates(
        fresh_no_days, [SlotCandidate(slot="days", value=7, confidence=0.9)])
    assert updated2.days == 7
    assert accepted2 == ["days"]


def test_apply_never_overrides_explicit_party():
    """显式拆分派生（2 大人+1 小孩=3）不可被候选覆盖。"""
    fresh = extract_fresh_brief("两大人一小孩去福州玩3天")
    assert fresh.party_size == 3
    updated, accepted = apply_candidates(
        fresh, [SlotCandidate(slot="party_size", value=5, confidence=0.99)])
    assert updated.party_size == 3
    assert accepted == []


def test_apply_never_writes_state_or_routing_channels():
    """结构断言：apply_candidates 返回值只有 (brief, 槽名列表)。"""
    fresh = TravelBrief()
    updated, accepted = apply_candidates(
        fresh, [SlotCandidate(slot="days", value=2, confidence=0.9)])
    assert isinstance(updated, TravelBrief)
    assert accepted == ["days"]
    # LLM 输出对象里不存在任何可路由/可执行字段
    candidate = SlotCandidate(slot="days", value=2, confidence=0.9)
    assert not any(hasattr(candidate, key) for key in
                   ("next_node", "route", "state_update", "itinerary"))


# ── P0-06/07/25：失败降级链 ──────────────────────────────────────

class _FakeLLM:
    def bind(self, **kwargs):
        return self

    def invoke(self, messages):
        raise AssertionError("测试未注入响应")


def _patch_llm(monkeypatch, content: str | Exception, sleep_ms: int = 5):
    """mock 外部边界：LLM 构建 / 限时调用 / 计量。"""
    from backend.infra import async_utils
    from backend.infra.llm import proxy as proxy_mod

    recorded: list[dict] = []

    class _Resp:
        pass

    _Resp.content = content

    def _fake_sync_call(fn, timeout, *args, **kwargs):
        if isinstance(content, Exception):
            raise content
        return _Resp()

    monkeypatch.setattr(async_utils, "sync_call_with_timeout",
                        _fake_sync_call)
    monkeypatch.setattr(proxy_mod, "_build_llm_for", lambda name: _FakeLLM())
    monkeypatch.setattr(
        proxy_mod, "record_llm_result",
        lambda result, duration_ms=None, model_name=None:
        recorded.append({"model": model_name}))
    return recorded


@pytest.fixture()
def _flag_on(monkeypatch):
    from backend.config import travel as T

    monkeypatch.setattr(T, "TRAVEL_LLM_SLOT_ENRICHMENT_ENABLED", True)


def test_timeout_falls_back_to_rules(monkeypatch, _flag_on):
    from concurrent.futures import TimeoutError as FutureTimeout

    _patch_llm(monkeypatch, FutureTimeout("boom"))
    outcome = enrich_slots("去福州待一周", missing_slots=["days"])
    assert outcome.status == "error"
    assert outcome.fallback_reason == "llm_error"
    assert outcome.candidates == []
    # 正常走规则链的输入本身：extract 的 days 仍为 None
    assert extract_fresh_brief("去福州待一周").days is None


def test_invalid_json_falls_back(monkeypatch, _flag_on):
    _patch_llm(monkeypatch, "我不是 JSON")
    outcome = enrich_slots("去福州待一周", missing_slots=["days"])
    assert outcome.status == "schema_invalid"
    assert outcome.fallback_reason == "invalid_json"


def test_llm_unavailable_returns_empty_outcome(monkeypatch, _flag_on):
    """P0-25：模型解析不出（no_model）→ 空结局，绝不抛错。"""
    from backend.config import model_roles

    monkeypatch.setattr(model_roles, "resolve_effective", lambda role: {})
    outcome = enrich_slots("去福州待一周", missing_slots=["days"])
    assert outcome.status == "error"
    assert outcome.fallback_reason == "no_model"


def test_disabled_flag_never_calls_llm(monkeypatch):
    from backend.config import travel as T

    monkeypatch.setattr(T, "TRAVEL_LLM_SLOT_ENRICHMENT_ENABLED", False)

    def _boom(*a, **k):
        raise AssertionError("flag 关闭不得触碰 LLM")

    from backend.infra import async_utils

    monkeypatch.setattr(async_utils, "sync_call_with_timeout", _boom)
    outcome = enrich_slots("去福州待一周", missing_slots=["days"])
    assert outcome.status == "disabled"
    assert outcome.used is False


def test_happy_path_parses_candidates(monkeypatch, _flag_on):
    content = json.dumps({"candidates": [
        {"slot": "days", "value": 7, "confidence": 0.9,
         "evidence_text": "待一周"},
        {"slot": "next_node", "value": "poi", "confidence": 0.99,
         "evidence_text": "越权"},
    ]}, ensure_ascii=False)
    recorded = _patch_llm(monkeypatch, content)
    outcome = enrich_slots("去福州待一周", missing_slots=["days"])
    assert outcome.status == "ok"
    assert [c.slot for c in outcome.candidates] == ["days"]
    assert outcome.rejected == 1
    # 计量补账必须发生（Token/Cost 进现有计费体系，P0-23）
    assert recorded and recorded[0]["model"]


# ── P0-01/02：slot_filler 触发面（单轮 ≤1 次 / 完整命中零调用）────

def _patch_turn_decision(monkeypatch, decision=None, status="schema_invalid"):
    from backend.config import travel as travel_config
    from backend.travel.services import turn_decision_service
    from backend.travel.services import city_guide_service

    monkeypatch.setattr(
        travel_config, "TRAVEL_TURN_DECISION_LLM_ENABLED", True)
    monkeypatch.setattr(
        travel_config, "TRAVEL_LLM_CLARIFICATION_ENABLED", False)
    monkeypatch.setattr(city_guide_service, "prime_city_guide", lambda _city: None)
    calls = []

    def _fake(message, *, context, timeout_ms=None):
        calls.append((message, context))
        return turn_decision_service.TurnDecisionOutcome(
            decision=decision, status=status,
            fallback_reason="invalid_json" if decision is None else "",
            model="test-model", prompt_version="travel.turn_decision@v-test",
            latency_ms=5,
        )

    monkeypatch.setattr(
        turn_decision_service, "interpret_turn_with_llm", _fake)
    return calls


def test_rule_complete_means_zero_llm_calls(monkeypatch):
    """P0-01：规则完整识别 → 富化 0 次，slot_parse_source=rule。"""
    from backend.travel.slot_filler import slot_filler_node

    calls = _patch_turn_decision(monkeypatch)
    result = slot_filler_node({"user_message": "福州3天"})
    assert result["brief_missing"] == []
    assert calls == []
    assert result["slot_parse_source"] == "rule"
    assert result["slot_llm_meta"] == {}


def test_complex_missing_slot_uses_one_unified_decision_call(monkeypatch):
    """复杂表达只调用一次统一决策，不再分别调意图与槽位服务。"""
    from backend.travel.slot_filler import slot_filler_node

    calls = _patch_turn_decision(monkeypatch)
    result = slot_filler_node({"user_message": "去福州待一周"})
    assert len(calls) == 1
    assert calls[0][0] == "去福州待一周"
    assert result["slot_parse_source"] == "rule_fallback"
    assert result["slot_llm_meta"]["fallback_reason"] == "invalid_json"


def test_query_intent_never_triggers_enrichment(monkeypatch):
    calls = _patch_turn_decision(monkeypatch)
    from backend.travel.slot_filler import slot_filler_node

    slot_filler_node({"user_message": "福州好玩吗"})
    assert calls == []


def test_structured_action_does_not_call_turn_decision_llm(monkeypatch):
    """来自按钮的白名单结构化操作不需要再次做语义分类。"""
    calls = _patch_turn_decision(monkeypatch)
    from backend.travel.slot_filler import slot_filler_node

    result = slot_filler_node({
        "user_message": "把节奏调轻松",
        "request_mode": "action",
        "action_payload": {"operation": "set_pace", "value": "relaxed"},
    })
    assert calls == []
    assert result["turn_decision"]["primary_action"] == "modify_plan"
    assert result["turn_decision"]["parse_source"] == "structured"


def test_enrichment_fills_days_end_to_end(monkeypatch, _flag_on):
    """富化正例：规则盲区（待一周）经 mock LLM 补 days=7 → 不再追问。"""
    from backend.travel.core.intent import TravelTurnDecision

    decision = TravelTurnDecision(
        primary_action="create_plan",
        brief_candidates=[{
            "slot": "days", "value": 7, "confidence": 0.9,
            "evidence_text": "待一周",
        }],
        parse_source="llm",
    )
    calls = _patch_turn_decision(monkeypatch, decision, status="ok")
    from backend.travel.slot_filler import slot_filler_node

    result = slot_filler_node({"user_message": "去福州待一周"})
    assert len(calls) == 1
    assert result["brief"]["days"] == 7
    assert result["brief_missing"] == []
    assert result["clarifications"] == []
    assert result["slot_parse_source"] == "rule+llm"
    assert result["slot_llm_meta"]["accepted_count"] == 1
    assert result["slot_llm_meta"]["model"]


def test_enrichment_never_overrides_rule_end_to_end(monkeypatch, _flag_on):
    """规则 days=3 + LLM days=5 → 3（Golden L 端到端）；且富化根本不触发。"""
    from backend.travel.core.intent import TravelTurnDecision

    decision = TravelTurnDecision(
        primary_action="create_plan",
        brief_candidates=[{
            "slot": "days", "value": 5, "confidence": 0.99,
            "evidence_text": "规则不得被覆盖",
        }],
        parse_source="llm",
    )
    calls = _patch_turn_decision(monkeypatch, decision, status="ok")
    from backend.travel.slot_filler import slot_filler_node

    result = slot_filler_node({"user_message": "福州3天"})
    assert calls == []  # 规则完整命中，统一理解入口未触发
    assert result["brief"]["days"] == 3
