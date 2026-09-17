"""customer_service/experts/knowledge.py — KnowledgeExpert

知识问答 Expert：调用 CSKnowledgeService 执行 RAG 问答。
从 graph/nodes.py cs_knowledge_node 提取核心逻辑。

设计参考: docs/customer-service/langgraph-multi-expert-design.md §6.1
"""
from __future__ import annotations

from typing import Any

from backend.customer_service.experts.base import ExpertResult, ExpertStatus
from backend.shared.logger import logger


def execute_knowledge(
    user_message: str,
    cs_route: dict,
    session_id: str,
) -> ExpertResult:
    """KnowledgeExpert 核心逻辑。

    Args:
        user_message: 用户原始问题
        cs_route: CS Router 输出（含 intent + kb_ids）
        session_id: 会话 ID

    Returns:
        ExpertResult — status=success 时含 response_draft + evidence
    """
    from backend.observability.metrics import record_cs_rag_status

    kb_ids = cs_route.get("kb_ids", [])
    intent = cs_route.get("intent", "k_faq")

    logger.info(
        "[KnowledgeExpert] intent=%s kb_ids=%s question=%s...",
        intent, kb_ids, user_message[:60],
    )

    from backend.customer_service.knowledge import get_knowledge_service

    service = get_knowledge_service()
    result = service.answer(
        question=user_message,
        kb_ids=kb_ids if kb_ids else None,
        session_id=session_id,
    )

    record_cs_rag_status("hit" if result.answer else "miss")

    evidence = []
    if hasattr(result, "source_documents") and result.source_documents:
        for doc in result.source_documents[:5]:
            evidence.append({
                "source": doc.get("source", doc.get("doc_id", "unknown")),
                "score": doc.get("score", 0.0),
            })

    response_draft = result.answer
    if not response_draft:
        response_draft = "抱歉，暂时无法找到相关信息。建议您转接人工客服获取更详细的帮助。"

    logger.info(
        "[KnowledgeExpert] decision=%s conf=%.2f answer_len=%d",
        result.decision.value, result.confidence, len(response_draft),
    )

    return ExpertResult(
        expert="knowledge",
        status=ExpertStatus.SUCCESS.value,
        response_draft=response_draft,
        evidence=evidence,
        data={
            "decision": result.decision.value,
            "confidence": result.confidence,
            "kb_ids": result.kb_ids,
        },
    )


def knowledge_expert_node(state: dict[str, Any]) -> dict[str, Any]:
    """CS Graph KnowledgeExpert 节点函数。

    从 CSGraphState 读取输入，调用 execute_knowledge，
    将结果写入 last_expert_result + expert_history。
    """
    from backend.config.customer_service import CS_EXPERT_TIMEOUT_S
    from backend.customer_service.experts.base import run_expert_safely

    user_message = state.get("user_message", "")
    cs_route = state.get("cs_route", {})
    session_id = state.get("session_id", "default")

    result = run_expert_safely(
        expert_name="knowledge",
        fn=lambda _state: execute_knowledge(user_message, cs_route, session_id),
        state=state,
        timeout_s=CS_EXPERT_TIMEOUT_S,
    )

    expert_history = list(state.get("expert_history", []))
    expert_history.append({
        "expert": "knowledge",
        "status": result.get("status", "failed"),
        "duration_ms": result.get("duration_ms", 0),
    })

    return {
        "last_expert_result": dict(result),
        "expert_history": expert_history,
    }
