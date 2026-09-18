"""types.py — CSUnderstanding 单一 Pydantic 契约（规划稿 §五）。

next_action 枚举与评测集 cs-v2 的 expected.next_action 同域
（answer/clarify/refuse/handoff/propose/propose_with_gap），评测断言直接可用。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from backend.customer_service.router.types import CSDomain


class Sentiment(str, Enum):
    """情绪只影响语气与转人工优先级，不直接触发业务写操作（规划稿 §七）。"""
    CALM = "calm"
    DISSATISFIED = "dissatisfied"
    ANGRY = "angry"


class Urgency(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class NextAction(str, Enum):
    """与评测集 cs-v2 expected.next_action 同域。"""
    ANSWER = "answer"
    CLARIFY = "clarify"
    REFUSE = "refuse"
    HANDOFF = "handoff"
    PROPOSE = "propose"
    PROPOSE_WITH_GAP = "propose_with_gap"


class DecisionLayer(str, Enum):
    RULE = "rule"
    LLM = "llm"


class EntityType(str, Enum):
    ORDER_ID = "order_id"
    TRACKING_NO = "tracking_no"
    AMOUNT = "amount"
    EMAIL = "email"
    PHONE = "phone"
    PRODUCT = "product"


class EntitySpan(BaseModel):
    """实体抽取结果。

    value = 从规范化文本原样截取（零改写：形近错别字原样保留）；
    match_value = 匹配用归一值（仅大小写归一，订单号场景），与 value 分离
    以保证「改写」在契约上可见、可审计。
    """
    type: EntityType
    value: str
    start: int
    end: int
    match_value: Optional[str] = None
    source: str = "normalized"

    def match(self) -> str:
        return self.match_value or self.value


class CSUnderstanding(BaseModel):
    """单轮输入的统一理解结果 — 规划稿 §五 契约的落地形态。

    domain/intent/confidence 来自 CS Router（如调用方已算好）；
    实体/槽位/情绪/紧迫度/风险来自本包规则层；decision_layer 标记决策来源，
    所有路由 Trace 必须可查（P1 完成标准）。
    """
    raw_text: str = ""
    normalized_text: str = ""
    domain: CSDomain = CSDomain.UNKNOWN
    intent: str = "unknown"
    confidence: float = 0.0
    entities: list[EntitySpan] = Field(default_factory=list)
    missing_slots: list[str] = Field(default_factory=list)
    sentiment: Sentiment = Sentiment.CALM
    urgency: Urgency = Urgency.NORMAL
    risk_level: str = "low"
    next_action: NextAction = NextAction.ANSWER
    decision_layer: DecisionLayer = DecisionLayer.RULE
    signals: dict[str, Any] = Field(
        default_factory=dict,
        description="命中的规则名清单（情绪/紧迫/风险升级依据，可观测）",
    )

    def entity_values(self, etype: EntityType | str) -> list[str]:
        """指定类型的 match_value 列表（订单号等按归一值匹配）。"""
        want = etype.value if isinstance(etype, EntityType) else str(etype)
        return [e.match() for e in self.entities if e.type.value == want]

    def model_dump_contract(self) -> dict:
        """规划稿 §五 契约键序输出（对齐示例字段名）。"""
        d = self.model_dump(mode="json")
        return {
            "raw_text": d["raw_text"],
            "normalized_text": d["normalized_text"],
            "domain": d["domain"],
            "intent": d["intent"],
            "confidence": d["confidence"],
            "entities": d["entities"],
            "missing_slots": d["missing_slots"],
            "sentiment": d["sentiment"],
            "urgency": d["urgency"],
            "risk_level": d["risk_level"],
            "next_action": d["next_action"],
            "decision_layer": d["decision_layer"],
        }
