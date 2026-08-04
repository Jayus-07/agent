"""
skills/rag/skill.py — RAG Skill

Capability: rag.search — 用户问题 → 向量+BM25混合检索 → 带引用标注的答案
"""

from backend.orchestration.tools import search_knowledge_tool
from backend.orchestration.skills.base import BaseSkill
from backend.shared.logger import logger


class RAGSkill(BaseSkill):
    """知识库检索 Skill"""

    name = "rag"
    capabilities = ["rag.search"]
    description = "从跨境电商知识库中检索 SOP/规范/FAQ/Listing指南等非结构化内容"
    params_schema = {"question": "检索问题"}
    examples = [{"question": "Amazon FBA发货的标准操作流程SOP"}]

    @property
    def _tool_fn(self):
        return search_knowledge_tool


async def rag_skill_node(state: dict) -> dict:
    """LangGraph 节点适配器"""
    skill = RAGSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(state.get("current_step_id", ""), {}).get("capability", "rag.search")
    logger.info(f"[RAG Skill] cap={cap} step={state.get('current_step_id')}")
    return await skill.execute(state, step_capability=cap)
