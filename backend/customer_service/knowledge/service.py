"""customer_service/knowledge/service.py — 客服知识问答服务

封装 RAGPipeline，添加:
  - kb_ids 多知识库指定
  - 置信度门控（CSAnswerDecision）
  - CS 特定的结果封装
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from backend.customer_service.knowledge.answer_decision import (
    CSAnswerDecision,
    Decision,
)
from backend.shared.logger import logger


@dataclass
class CSKnowledgeResult:
    answer: str
    decision: Decision
    confidence: float = 0.0
    kb_ids: list[str] = field(default_factory=list)
    suffix: str = ""
    error: Optional[str] = None


class CSKnowledgeService:
    """客服知识问答服务。"""

    def answer(
        self,
        question: str,
        kb_ids: list[str] | None = None,
        session_id: str = "default",
    ) -> CSKnowledgeResult:
        """知识问答入口。

        Args:
            question: 用户问题
            kb_ids: 指定的知识库 ID 列表（来自 CSRouteResult）
            session_id: 会话 ID
        """
        if not kb_ids:
            kb_ids = ["cs_faq"]

        try:
            from backend.rag.pipeline import RAGPipeline
            pipeline = RAGPipeline()

            primary_kb = kb_ids[0] if kb_ids else "cs_faq"
            logger.info(
                f"[CSKnowledge] 问答: question={question[:60]}... "
                f"kb_ids={kb_ids}"
            )

            answer = pipeline.ask(
                question=question,
                session_id=session_id,
                kb_id=primary_kb,
                kb_ids=kb_ids,
            )

            meta = getattr(pipeline, "last_answer_meta", {}) or {}
            confidence = meta.get("confidence", 0.5)
            has_evidence = meta.get("can_answer", True) and bool(answer and answer.strip())

            cs_decision = CSAnswerDecision.decide(confidence, has_evidence)

            final_answer = answer or ""
            if cs_decision.decision == Decision.CAUTIOUS:
                final_answer = answer + cs_decision.suffix
            elif cs_decision.decision == Decision.REFUSE:
                final_answer = cs_decision.suffix

            return CSKnowledgeResult(
                answer=final_answer,
                decision=cs_decision.decision,
                confidence=confidence,
                kb_ids=kb_ids,
                suffix=cs_decision.suffix,
            )

        except Exception as e:
            logger.error(f"[CSKnowledge] 问答异常: {e}", exc_info=True)
            return CSKnowledgeResult(
                answer="系统繁忙，请稍后重试或联系人工客服。",
                decision=Decision.REFUSE,
                kb_ids=kb_ids,
                error=str(e),
            )


_service_instance: CSKnowledgeService | None = None


def get_knowledge_service() -> CSKnowledgeService:
    global _service_instance
    if _service_instance is None:
        _service_instance = CSKnowledgeService()
    return _service_instance
