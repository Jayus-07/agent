"""customer_service/knowledge/service.py — 客服知识问答服务

封装 RAGPipeline，添加:
  - kb_ids 多知识库指定
  - 置信度门控（CSAnswerDecision）
  - CS 特定的结果封装
"""
from __future__ import annotations

import time
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
    # C4 FAQ 双轨标记（2026-10-04）：True=FAQ 精准层直返，未进 RAG/LLM
    faq_hit: bool = False
    faq_id: Optional[int] = None
    latency_ms: int = 0


class CSKnowledgeService:
    """客服知识问答服务。"""

    def answer(
        self,
        question: str,
        kb_ids: list[str] | None = None,
        session_id: str = "default",
        user_id: str = "",
        tenant_id: str = "",
    ) -> CSKnowledgeResult:
        """知识问答入口。

        Args:
            question: 用户问题
            kb_ids: 指定的知识库 ID 列表（来自 CSRouteResult）
            session_id: 会话 ID
            user_id: 认证坐席/客服用户 id（来自 cs_context.authenticated_user_id）；
                空 = 未认证会话，按 demo 命名空间隔离，绝不共享长期记忆
            tenant_id: 租户 id（来自 cs_context.tenant_id）
        """
        if not kb_ids:
            kb_ids = ["cs_faq"]
        # E2（2026-09-23）：身份贯通——认证用户按真实身份隔离 L2/L3 记忆；
        # 无身份不得落共享 "default"（所有未知用户共用一个长期记忆空间），
        # 显式 demo 命名空间按会话隔离
        if not user_id:
            user_id = f"cs-anon:{session_id}"

        # C3/C4 FAQ 双轨（2026-10-04）：精准命中直返，不进 RAG/LLM——
        # TTFT 从秒级降到百毫级、成本≈0；未命中原样落回既有 RAG 链。
        faq_t0 = time.monotonic()
        try:
            from backend.customer_service.faq import get_faq_store
            faq = get_faq_store().match(question)
        except Exception as faq_err:
            logger.warning(f"[CSKnowledge] FAQ 层故障（降级走 RAG 链）: {faq_err}")
            try:
                from backend.customer_service.faq import cs_faq_layer_failures_total
                cs_faq_layer_failures_total.inc()
            except Exception:
                pass  # 观测旁路失败不影响降级主链
            faq = None
        if faq is not None:
            latency_ms = int((time.monotonic() - faq_t0) * 1000)
            logger.info(
                "[CSKnowledge] FAQ 命中: faq_id=%s score=%.2f by=%s latency=%dms question=%s...",
                faq.faq_id, faq.score, faq.matched_by, latency_ms, question[:60],
            )
            return CSKnowledgeResult(
                answer=faq.answer,
                decision=Decision.ANSWER,
                confidence=round(max(faq.score, 0.9), 2),
                kb_ids=kb_ids,
                suffix="",
                faq_hit=True,
                faq_id=faq.faq_id,
                latency_ms=latency_ms,
            )

        try:
            # 必须用单例：RAGPipeline.__init__ 会全量加载文档/重建 BM25/加载向量库，
            # 每请求新建一次的代价是秒级以上，且绕过 answer_cache 的失效机制
            from backend.rag.pipeline import get_rag_pipeline
            pipeline = get_rag_pipeline()

            primary_kb = kb_ids[0] if kb_ids else "cs_faq"
            logger.info(
                f"[CSKnowledge] 问答: question={question[:60]}... "
                f"kb_ids={kb_ids}"
            )

            outcome = pipeline.ask_result(
                question=question,
                session_id=session_id,
                kb_id=primary_kb,
                kb_ids=kb_ids,
                # 对客知识问答：检索授权收敛到 audience=="customer" 库（cs_*）
                subject_type="customer",
                user_id=user_id,
                tenant_id=tenant_id,
            )
            answer = outcome.answer

            # D1-6：meta 随请求级返回值带回，不再读 pipeline 单例属性
            # （并发请求互相覆盖串扰）
            meta = outcome.answer_meta or {}
            # B9（2026-09-29 迁移）：置信度三档门禁 = ≥0.85 直接回答 /
            # 0.60~0.85 CAUTIOUS / <0.60 拒答+建议人工（由
            # CSAnswerDecision 按 CS_CONFIDENCE_ANSWER/CAUTIOUS 执行）。
            # META 缺失（模型 <!--META--> 注释遵循度不稳）= 置信度与证据
            # 均不可信——P3.5 时代的「can_answer 默认 True → 兜底 0.65
            # 放行 CAUTIOUS」使两道门禁对该流量形同虚设，现收紧为 REFUSE
            # （设计方案 §4.3 兜底值必须保守 / §14.1 兜底收紧 REFUSE），
            # 并打兜底指标：兜底率上升 = META 遵循度劣化信号。
            meta_confidence = meta.get("confidence")
            if meta_confidence is not None:
                confidence = float(meta_confidence)
                has_evidence = bool(meta.get("can_answer", True)) and bool(
                    answer and answer.strip()
                )
            else:
                from backend.observability.metrics import (
                    record_cs_knowledge_meta_fallback,
                )

                record_cs_knowledge_meta_fallback()
                confidence = 0.0
                has_evidence = False

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
