"""
skills/rag/skill.py — RAG Skill

Capability: rag.search — 用户问题 → 向量+BM25混合检索 → 带引用标注的答案
"""

from backend.orchestration.graph.sse_event_sink import emit_sse_progress
from backend.orchestration.tools import search_knowledge_tool
from backend.skills.base import BaseSkill
from backend.shared.logger import logger


_KNOWLEDGE_FOLLOWUP_MARKERS = (
    "刚才提到", "刚才说", "刚才列出", "刚刚提到", "刚刚说",
    "上面提到", "上面说", "前面提到", "前面说",
)
_KNOWLEDGE_QUERY_INSTRUCTION_MARKERS = (
    "，逐条列出", "，逐项列出", "，逐个列出", "，并明确检查",
    "，明确检查", "；不要补造内容", "，不要补造内容",
)


def _compact_knowledge_question(question: str) -> str:
    """长问题只保留检索主题，去掉要求回答格式的尾句。"""
    original = (question or "").strip()
    if len(original) < 60:
        return original
    for marker in _KNOWLEDGE_QUERY_INSTRUCTION_MARKERS:
        if marker in original:
            topic = original.split(marker, maxsplit=1)[0].strip(" ，；。")
            if topic:
                return f"{topic} 原文出处"
    return original


def _previous_user_question(messages: list, current_question: str) -> str:
    """取当前追问之前最近一条有内容的用户问题，不读助手生成的答案。"""
    current = (current_question or "").strip()
    for message in reversed(messages or []):
        if isinstance(message, dict):
            role = message.get("role")
            content = message.get("content")
        else:
            role = getattr(message, "type", "")
            content = getattr(message, "content", None)
        if role not in ("human", "user") or not isinstance(content, str):
            continue
        question = content.strip()
        if (
            len(question) < 8
            or question == current
            or any(marker in question for marker in _KNOWLEDGE_FOLLOWUP_MARKERS)
        ):
            continue
        return question
    return ""


class RAGSkill(BaseSkill):
    """知识库检索 Skill"""

    name = "rag"
    # STOP G M1 显式声明（禁止隐式默认）：检索答案 pipeline.ask -> str，
    # 给 LLM 阅读，text 型不套封套。
    output_type = "text"

    @property
    def _tool_fn(self):
        return search_knowledge_tool

    def _normalize_invocation_params(
        self, state: dict, capability: str, params: dict,
    ) -> dict:
        """补全 rag.search 的请求级参数（question / kb_id）。

        - question：Planner/FC 可能给必填字段留下空字符串；当前参数 schema
          只验证字段存在，空字符串会继续到 RAG 服务并被其 min_length 校验拒绝。
        - kb_id：direct 快路径 FC 只选 question，不会填 kb_id，工具落回
          "default" 空库；请求级 kb_id（用户显式指定的检索范围）必须从
          graph state 注入，语义与 supervisor/scheduler.py 的 plan 路径
          `state.get("kb_id", "default")` 一致。state 为空时保留 params
          原值（工具默认 default），不伪造检索范围。
        """
        if capability != "rag.search":
            return params

        updated = dict(params)
        state_kb = str(state.get("kb_id") or "").strip()
        if state_kb and updated.get("kb_id") != state_kb:
            updated["kb_id"] = state_kb

        raw_question = str(
            state.get("raw_query") or state.get("question") or ""
        ).strip()
        messages = state.get("messages") or []
        routing_context = state.get("routing_context") or {}
        product_id = state.get("product_id") or ""
        if (
            product_id == "agent"
            and isinstance(routing_context, dict)
            and routing_context.get("active_domain") == "knowledge"
            and any(marker in raw_question for marker in _KNOWLEDGE_FOLLOWUP_MARKERS)
        ):
            previous_question = _previous_user_question(
                messages, raw_question,
            )
            if previous_question:
                # 去掉回答格式指令，避免冗长句子拉低召回置信度。
                updated["question"] = _compact_knowledge_question(
                    previous_question,
                )
                return updated

        question = updated.get("question")
        if isinstance(question, str) and question.strip():
            return updated

        original_question = state.get("question")
        if not isinstance(original_question, str) or not original_question.strip():
            return updated

        return {**updated, "question": original_question}


async def rag_skill_node(state: dict) -> dict:
    """LangGraph 节点适配器"""
    skill = RAGSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(state.get("current_step_id", ""), {}).get("capability", "rag.search")
    logger.info(f"[RAG Skill] cap={cap} step={state.get('current_step_id')}")
    if cap == "rag.search":
        emit_sse_progress(
            node="rag_skill", phase="rag_search",
            message="正在检索知识库并核验资料",
            tool="rag.search",
        )
    result = await skill.execute(state, step_capability=cap)
    if cap == "rag.search":
        step_id = state.get("current_step_id")
        step_result = (
            (result.get("step_results") or {}).get(step_id, {})
            if isinstance(result, dict)
            else {}
        )
        request_context = state.get("request_context")
        product_id = (
            request_context.get("product_id")
            if isinstance(request_context, dict)
            else getattr(request_context, "product_id", None)
        ) or state.get("product_id") or "internal"
        if product_id == "agent" and step_result.get("status") == "success":
            try:
                from backend.orchestration.context.routing_context import (
                    mark_domain_turn,
                )

                mark_domain_turn(
                    state.get("tenant_id") or "",
                    state.get("user_id") or "",
                    state.get("product_session_id")
                    or state.get("session_id")
                    or "",
                    domain="knowledge",
                    intent="skill_execution",
                    action="rag.search",
                    pending_question="",
                )
            except Exception:
                logger.debug(
                    "[RAG Skill] 知识库执行上下文回写失败（软降级）",
                    exc_info=True,
                )
    return result
