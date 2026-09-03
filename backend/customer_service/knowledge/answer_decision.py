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


REFUSAL_MESSAGES: dict[str, str] = {
    "no_evidence": "抱歉，我暂时无法找到相关信息。建议联系人工客服获取帮助。",
    "low_confidence": "抱歉，我不太确定这个问题的答案。建议联系人工客服获取更准确的信息。",
}

CAUTIOUS_SUFFIX = "\n\n（以上信息仅供参考，如有疑问建议联系人工客服确认。）"
