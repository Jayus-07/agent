"""service.py — build_understanding 编排入口（纯规则，零 IO / 零 LLM）。

组合：normalize_query（事实源）→ 实体抽取 → 槽位 → 情绪/紧迫 → 风险升级
→ next_action 缺省推断。调用方可传入 CS Router 结果（CSRouteResult 或 dict）
以携带 intent/confidence；未传时保持 unknown，由路由层补齐。

next_action 缺省推断（接线层可按路由结果覆写）：
- 意图要求动作（requires_action）且有缺槽位 → clarify；
- 意图要求动作且槽位齐 → propose（确认卡语义，最终执行仍走审批/确认）；
- 其余 → answer。
"""
from __future__ import annotations

from typing import Any

from backend.customer_service.router.intents import INTENT_PROFILES
from backend.customer_service.router.types import CSDomain, CSRouteResult
from backend.customer_service.understanding.entities import extract_entities
from backend.customer_service.understanding.slots import compute_missing_slots
from backend.customer_service.understanding.signals import (
    detect_risk_hits,
    detect_sentiment,
    detect_urgency,
    escalate,
)
from backend.customer_service.understanding.types import (
    CSUnderstanding,
    DecisionLayer,
    NextAction,
)
from backend.security.input_guard.normalize import normalize_query


def _route_fields(cs_route: Any) -> tuple[str, str, float]:
    """从 CSRouteResult / dict / None 提取 (intent, risk_level, confidence)。"""
    if cs_route is None:
        return "unknown", "low", 0.0
    if isinstance(cs_route, CSRouteResult):
        return cs_route.intent, cs_route.risk_level, cs_route.confidence
    if isinstance(cs_route, dict):
        return (cs_route.get("intent", "unknown"),
                cs_route.get("risk_level", "low"),
                float(cs_route.get("confidence", 0.0) or 0.0))
    return "unknown", "low", 0.0


def build_understanding(raw_text: str,
                        cs_route: Any = None,
                        decision_layer: DecisionLayer = DecisionLayer.RULE,
                        ) -> CSUnderstanding:
    raw = raw_text or ""
    normalized = normalize_query(raw)
    entities = extract_entities(normalized)

    intent, route_risk, confidence = _route_fields(cs_route)
    profile = INTENT_PROFILES.get(intent)

    missing = compute_missing_slots(intent, entities)
    sentiment, sent_hits = detect_sentiment(normalized)
    urgency, urgent_hits = detect_urgency(normalized)
    risk_hits = detect_risk_hits(normalized)

    base_risk = profile.risk_level if profile else route_risk
    risk, escalated = escalate(base_risk, risk_hits)

    signals = {"sentiment": sent_hits, "urgency": urgent_hits, "risk": risk_hits}
    if escalated:
        signals["risk_escalated"] = True

    if profile is None:
        domain = CSDomain.UNKNOWN
        requires_action = False
    else:
        domain = profile.domain
        requires_action = profile.requires_action

    if not requires_action:
        next_action = NextAction.ANSWER
    elif missing:
        next_action = NextAction.CLARIFY
    else:
        next_action = NextAction.PROPOSE

    return CSUnderstanding(
        raw_text=raw,
        normalized_text=normalized,
        domain=domain,
        intent=intent,
        confidence=confidence,
        entities=entities,
        missing_slots=missing,
        sentiment=sentiment,
        urgency=urgency,
        risk_level=risk,
        next_action=next_action,
        decision_layer=decision_layer,
        signals=signals,
    )
