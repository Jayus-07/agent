"""P0 Golden 15 案例（2026-10-08 客服域 LLM 收口 · 任务书 §三十）。

每条案例映射任务书 P0 验收口径；只 mock 外部边界（LLM/订单服务/store）。
"""
from __future__ import annotations

import json

from backend.customer_service.understanding import semantic
from backend.customer_service.understanding.contracts import CSSlotCandidate


def _route(intent="unknown", confidence=0.0, **metadata):
    return {"intent": intent, "confidence": confidence, "metadata": dict(metadata)}


class _FakeStore:
    def __init__(self):
        self.saved = []

    def save(self, user_id, session_id, pending, tenant_id=""):
        self.saved.append(pending)


# ── #1 规则强命中：我要退款 → Action intent，LLM intent = 0 ──

def test_golden_1_strong_rule_refund_zero_llm(monkeypatch):
    called = []
    monkeypatch.setattr(
        "backend.customer_service.understanding.semantic.llm_intent_candidate",
        lambda *_: called.append(1) or ("t_logistics", 0.99),
    )
    route = _route(intent="as_refund", confidence=0.8)
    semantic.enrich_semantic_understanding(route, "我要退款")
    assert not called
    assert route["intent"] == "as_refund"


# ── #2 自然物流问题：耳机怎么还没到 → 语义槽位，无 order_id ──

def test_golden_2_natural_logistics_slots(monkeypatch):
    from backend.config import customer_service as cs_config

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
    route = _route()
    result = semantic.enrich_semantic_understanding(
        route, "我昨天买的那个耳机怎么还没到？",
    )
    assert route["intent"] == "t_logistics"
    slots = {s["name"]: s["value"] for s in route["metadata"]["semantic_slots"]}
    assert slots["product_reference"] == "耳机"
    assert slots["time_reference"] == "昨天买的"
    # LLM 不得制造 order_id（metadata 无 order_id 注入）
    assert "order_id" not in route["metadata"]
    assert result.intent_candidate == "t_logistics"


# ── #3 上下文订单引用：第二个 → ContextResolver 序数解析 ──

def test_golden_3_ordinal_reference_resolved_by_rules(monkeypatch):
    from backend.customer_service.context import context_resolver

    monkeypatch.setattr(
        context_resolver, "get_recent_business_context",
        lambda *_: {"recent_order_ids": ["DEMO-1001", "DEMO-1002"],
                    "source_intent": "t_order_status"},
    )
    r = context_resolver.resolve_turn_reference(
        "第二个", tenant_id="default", user_id="u1", session_id="s1",
    )
    assert r is not None and r.order_id == "DEMO-1002"
    # LLM 只产 ordinal 语义候选（这里由规则直接解析，无需 LLM）


# ── #4 退款对象不唯一：两个耳机订单 → 必须追问 ──

def test_golden_4_ambiguous_refund_candidates(monkeypatch):
    from backend.customer_service.context.semantic_slots import (
        resolve_semantic_slots,
    )

    monkeypatch.setattr(
        "backend.customer_service.service.order_service.get_order_service",
        lambda: type("S", (), {"list_recent_orders_with_products": staticmethod(
            lambda *_a, **_k: [
                {"order_no": "DEMO-1001", "product_names": "无线耳机",
                 "status": "shipped", "total_amount": 199, "created_at": "2026-10-07"},
                {"order_no": "DEMO-1002", "product_names": "蓝牙耳机",
                 "status": "shipped", "total_amount": 299, "created_at": "2026-10-01"},
            ],
        )})(),
    )
    r = resolve_semantic_slots(
        "default", "u1", [{"name": "product_reference", "value": "耳机"}],
    )
    assert r.ambiguous and len(r.candidates) == 2  # 追问点选，不自动选最近


# ── #5 写操作确认：我要退订单 A123 → proposal/pending，不立即执行 ──

def test_golden_5_action_goes_through_proposal(monkeypatch):
    from backend.customer_service.experts.action import execute_action
    from backend.customer_service.experts.base import ExpertResult

    captured = {}

    def _fake_proposal(user_id, intent, cs_route, session_id, store,
                       user_message="", tenant_id=""):
        captured["intent"] = intent
        captured["order_id"] = cs_route["metadata"].get("order_id")
        return ExpertResult(
            expert="action", status="success",
            response_draft="退款申请确认",
            data={
                "pending_action": {"action_id": "a1", "status": "pending_confirmation"},
                "confirmation_state": "pending_confirmation",
            },
        )

    monkeypatch.setattr(
        "backend.customer_service.experts.action._build_new_proposal",
        _fake_proposal,
    )
    monkeypatch.setattr(
        "backend.customer_service.security.permission.PermissionChecker.validate_user_identity",
        lambda state: "u1",
    )
    route = _route(intent="as_refund", confidence=0.8, order_id="DEMO-1001")
    result = execute_action("我要退订单 DEMO-1001", route, {"user_id": "u1"})
    assert result["data"]["confirmation_state"] == "pending_confirmation"
    assert captured["order_id"] == "DEMO-1001"


# ── #6/#7 模糊确认/取消：词表判定 + LLM 不改状态机 ──

def test_golden_6_fuzzy_confirm_by_vocab():
    from backend.customer_service.confirmation import (
        ConfirmationIntent,
        detect_confirmation_intent,
    )

    assert detect_confirmation_intent("可以，就这样") is ConfirmationIntent.CONFIRM


def test_golden_7_fuzzy_cancel_by_vocab_never_executes():
    from backend.customer_service import confirmation_flow
    from backend.customer_service.confirmation import (
        ConfirmationIntent,
        detect_confirmation_intent,
    )

    assert detect_confirmation_intent("算了，不弄了") is ConfirmationIntent.CANCEL
    # 状态机入口在 CANCEL 分支只做取消，不触碰执行（process_confirmation 规则路径）
    # LLM candidate 默认关 → 行为与词表完全一致
    assert confirmation_flow._llm_confirm_candidate("算了，不弄了") is ConfirmationIntent.NONE


def test_golden_7b_question_form_never_confirm(monkeypatch):
    """疑问语气绝不表态（「能退吗」≠ 确认）——即便 LLM candidate 开关打开。"""
    from backend.config import customer_service as cs_config
    from backend.customer_service import confirmation_flow
    from backend.customer_service.confirmation import ConfirmationIntent

    monkeypatch.setattr(cs_config, "CS_CONFIRM_LLM_CANDIDATE_ENABLED", True)
    assert confirmation_flow._llm_confirm_candidate("退款多久到账？") is ConfirmationIntent.NONE


# ── #8 转人工：正确进入 handoff，LLM 不改 handoff_state ──

def test_golden_8_handoff_by_rule_not_llm():
    from backend.customer_service.handoff import detect_handoff_trigger
    from backend.customer_service.handoff.handoff import TriggerType
    from backend.customer_service.vocab import CS_DOMAIN_PATTERNS

    # ① 域判定（现有能力）：「跟机器人说」命中 HUMAN pattern
    assert any(p.search("我不想跟机器人说了") for p in CS_DOMAIN_PATTERNS["HUMAN"])
    # ② expert 内触发归因（2026-10-08 补窄模式）：显式请求而非 auto 兜底
    trigger = detect_handoff_trigger("我不想跟机器人说了")
    assert trigger is not None
    assert trigger.trigger_type is TriggerType.EXPLICIT_REQUEST


# ── #9 投诉：severity candidate + 规则 policy ──

def test_golden_9_complaint_severity_rule_policy(monkeypatch):
    from backend.customer_service.service.complaint_service import ComplaintService

    svc = ComplaintService()
    # 投诉+太差了 = 2 命中 → high（规则矩阵决定，非 LLM）
    d = svc.detect("你们服务太差了，我要投诉")
    assert d.is_complaint
    assert d.severity == "high"

    # LLM 兜底路径：模型越权输出 critical（词表外）→ validator 拦截回规则
    class _FakeResp:
        content = '```json\n{"is_complaint": true, "severity": "critical"}\n```'

    monkeypatch.setattr(
        "backend.infra.llm.llm",
        type("L", (), {"invoke": staticmethod(lambda *_a, **_k: _FakeResp())})(),
    )
    d2 = svc.detect_with_llm_fallback("再不处理就没法用了")
    # validator 拒绝 critical → _llm_assess None → 回规则结果（low，未命中投诉词）
    assert d2.severity in ("low", "medium", "high")
    assert d2.severity != "critical"

    # 合法白名单 severity 被采纳为 candidate，但升级仍由规则矩阵执行
    class _FakeResp2:
        content = '{"is_complaint": true, "severity": "high"}'

    monkeypatch.setattr(
        "backend.infra.llm.llm",
        type("L", (), {"invoke": staticmethod(lambda *_a, **_k: _FakeResp2())})(),
    )
    d3 = svc.detect_with_llm_fallback("再不处理就没法用了")
    assert d3.is_complaint and d3.severity == "high"
    assert d3.matched_patterns == ["llm_fallback"]  # 溯源标记


# ── #10 普通知识问题：退货政策 → knowledge，不进 Action ──

def test_golden_10_policy_question_routes_knowledge():
    from backend.customer_service.router.coarse_router import CSCoarseRouter

    domain, conf, reason = CSCoarseRouter().classify("退货政策是什么")
    assert domain.value == "KNOWLEDGE"


# ── #11 复合问题：查物流+能不能退 → 分解，不直接执行退货 ──

def test_golden_11_compound_decompose_whitelist(monkeypatch):
    from backend.customer_service.experts.query import _INTENT_SERVICE_MAP

    class _FakeResp:
        content = json.dumps(["t_logistics", "as_refund"])

    monkeypatch.setattr("backend.infra.llm.llm", type("L", (), {"invoke": staticmethod(lambda *_a, **_k: _FakeResp())})())
    from backend.customer_service.experts.query import _llm_decompose_intents

    intents = _llm_decompose_intents("查下物流，顺便告诉我这个商品能退吗")
    # as_refund 不在 _INTENT_SERVICE_MAP 白名单 → 被丢弃（分解只允许查询族）
    assert intents == ["t_logistics"]
    assert "as_refund" not in _INTENT_SERVICE_MAP


# ── #12 意图 LLM timeout → 回规则，业务不断 ──

def test_golden_12_intent_llm_timeout_falls_back_to_rule(monkeypatch):
    """真实超时路径：sync_call_with_timeout 抛超时 → llm_invoke_once 捕获
    返回 None → 候选 None → 规则原样（P0-16）。"""
    from backend.config import customer_service as cs_config

    monkeypatch.setattr(cs_config, "CS_UNDERSTANDING_LLM_ENABLED", True)
    monkeypatch.setattr(cs_config, "CS_SLOT_LLM_ENABLED", False)

    def _timeout(*_a, **_k):
        raise TimeoutError("simulated llm timeout")

    monkeypatch.setattr(
        "backend.infra.async_utils.sync_call_with_timeout", _timeout,
    )
    route = _route()
    result = semantic.enrich_semantic_understanding(route, "帮我看看那个东西到哪了")
    assert route["intent"] == "unknown"  # 规则原样，业务不断
    assert result.source.value == "rule"


# ── #14 非法 JSON → fallback ──

def test_golden_14_invalid_json_rejected():
    from backend.customer_service.understanding.validator import extract_json_object

    assert extract_json_object("这不是 JSON") is None
    assert extract_json_object('{"intent": "t_logistics", broken') is None
    assert extract_json_object('```json\n{"intent": "t_logistics", "confidence": 0.8}\n```') == {
        "intent": "t_logistics", "confidence": 0.8,
    }


# ── #15 越权字段：只消费白名单，其余全丢弃 ──

def test_golden_15_illegal_fields_dropped(monkeypatch):
    from backend.config import customer_service as cs_config

    class _FakeResp:
        # 模型越权输出：execute/order_id/handoff_state 全部必须被丢弃
        content = json.dumps({
            "intent": "as_refund",
            "execute": True,
            "order_id": "FAKE123",
            "handoff_state": "human_active",
            "next_node": "action",
        })

    monkeypatch.setattr(cs_config, "CS_UNDERSTANDING_LLM_ENABLED", True)
    monkeypatch.setattr(cs_config, "CS_SLOT_LLM_ENABLED", False)
    monkeypatch.setattr(
        "backend.infra.llm.llm",
        type("L", (), {"invoke": staticmethod(lambda *_a, **_k: _FakeResp())})(),
    )
    from backend.customer_service.understanding.llm_intent_fallback import (
        llm_intent_candidate,
    )

    # intent 在白名单 → 采纳 intent 本身；execute/order_id/handoff_state/
    # next_node 不在输出契约 → 无处落地（返回值只有 intent+confidence）
    result = llm_intent_candidate("帮我退款")
    assert result is not None and result[0] == "as_refund"
