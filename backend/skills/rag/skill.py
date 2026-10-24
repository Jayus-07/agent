"""
skills/rag/skill.py — RAG Skill

Capability: rag.search — 用户问题 → 向量+BM25混合检索 → 带引用标注的答案
"""

from backend.orchestration.graph.sse_event_sink import emit_sse_progress
from backend.orchestration.tools import search_knowledge_tool
from backend.skills.base import BaseSkill
from backend.shared.logger import logger


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
    return await skill.execute(state, step_capability=cap)
