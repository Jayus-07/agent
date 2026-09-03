"""customer_service/graph/nodes.py — 客服 LangGraph 节点

cs_knowledge_node: Phase 2 — 知识问答（RAG）
cs_pending_node:   Phase 3-5 stub — 非知识路径的占位节点
"""
from __future__ import annotations

import time

from backend.shared.logger import logger


def cs_knowledge_node(state: dict) -> dict:
    """客服知识问答节点 — 调用 CSKnowledgeService 执行 RAG 问答。

    输入: state.question, state.cs_context.cs_route
    输出: final_answer, cs_context (含 answer_meta)
    """
    question = state.get("question", "")
    cs_context = dict(state.get("cs_context", {}))
    cs_route = cs_context.get("cs_route", {})

    kb_ids = cs_route.get("kb_ids", [])
    intent = cs_route.get("intent", "k_faq")
    session_id = "default"

    logger.info(
        f"[CSKnowledgeNode] intent={intent} kb_ids={kb_ids} "
        f"question={question[:60]}..."
    )

    try:
        from backend.customer_service.knowledge import get_knowledge_service

        service = get_knowledge_service()
        result = service.answer(
            question=question,
            kb_ids=kb_ids if kb_ids else None,
            session_id=session_id,
        )

        cs_context["answer_meta"] = {
            "decision": result.decision.value,
            "confidence": result.confidence,
            "kb_ids": result.kb_ids,
        }

        logger.info(
            f"[CSKnowledgeNode] decision={result.decision.value} "
            f"conf={result.confidence:.2f} answer_len={len(result.answer)}"
        )

        return {
            "final_answer": result.answer,
            "cs_context": cs_context,
        }

    except Exception as e:
        logger.error(f"[CSKnowledgeNode] 执行异常: {e}", exc_info=True)
        return {
            "final_answer": "系统繁忙，请稍后重试或联系人工客服。",
            "cs_context": cs_context,
        }


def cs_pending_node(state: dict) -> dict:
    """客服待处理节点 — Phase 3-5 的占位节点。

    非知识路径（business_query / business_action / complaint_flow / human_handoff）
    在 Phase 2 暂时由此节点承接，输出提示信息。
    """
    cs_context = dict(state.get("cs_context", {}))
    cs_route = cs_context.get("cs_route", {})
    route_path = cs_route.get("route_path", "knowledge_query")
    intent = cs_route.get("intent", "unknown")

    logger.info(
        f"[CSPendingNode] route_path={route_path} intent={intent} "
        f"(Phase 3-5 stub)"
    )

    placeholder = (
        "## 客服助手\n\n"
        f"您的问题已分类为 **{intent}**，相关功能正在建设中。\n\n"
        "当前可用功能：知识问答（FAQ、政策、产品、售后流程等）。\n"
        "如需人工服务，请输入「转人工」。"
    )

    return {
        "final_answer": placeholder,
        "cs_context": cs_context,
    }
