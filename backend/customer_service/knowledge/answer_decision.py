"""customer_service/knowledge/answer_decision.py — 回答决策逻辑

根据 RAG 置信度和证据情况决定回答策略:
  - answer: 高置信度，直接回答
  - cautious: 中置信度，附带保留意见
  - refuse: 低置信度/无证据，拒绝回答并引导转人工
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Decision(str, Enum):
    ANSWER = "answer"
    CAUTIOUS = "cautious"
    REFUSE = "refuse"


@dataclass
class CSAnswerDecision:
    decision: Decision
    suffix: str = ""

    @staticmethod
    def decide(confidence: float, has_evidence: bool) -> CSAnswerDecision:
        from backend.config.customer_service import (
            CS_CONFIDENCE_ANSWER,
            CS_CONFIDENCE_CAUTIOUS,
        )

        if not has_evidence:
            return CSAnswerDecision(
                decision=Decision.REFUSE,
                suffix=REFUSAL_MESSAGES["no_evidence"],
            )

        if confidence >= CS_CONFIDENCE_ANSWER:
            return CSAnswerDecision(decision=Decision.ANSWER)

        if confidence >= CS_CONFIDENCE_CAUTIOUS:
            return CSAnswerDecision(
                decision=Decision.CAUTIOUS,
                suffix=CAUTIOUS_SUFFIX,
            )

        return CSAnswerDecision(
            decision=Decision.REFUSE,
            suffix=REFUSAL_MESSAGES["low_confidence"],
        )


# 拒答话术（2026-10-04 对话体验改造 T2）：与寒暄同一人设口吻，不出现
# 主动转人工引导——人工仅在用户主动或 C8 必转时机（V5）；"已记录"是真实
# 动作（拒答已落 ai.cs_faq_query_log matched=false，缺口周检闭环消费）。
REFUSAL_MESSAGES: dict[str, str] = {
    "no_evidence": "这个问题我这边暂时没查到确切答案，已经记录下来了。",
    "low_confidence": "这个问题我没查到足够确切的依据，先不给您不确定的回复，已经记录下来了。",
}

CAUTIOUS_SUFFIX = "\n\n（以上信息仅供参考，如有疑问建议联系人工客服确认。）"
