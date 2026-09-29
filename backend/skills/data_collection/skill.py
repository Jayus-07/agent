"""skills/data_collection/skill.py — Data Collection Skill. Capability: data.collect"""
from backend.data_collection.tool import data_collection_tool
from backend.skills.base import BaseSkill
from backend.shared.logger import logger


class DataCollectionSkill(BaseSkill):
    """数据采集 Skill — 从外部数据源采集、清洗、分析并写入数据库"""

    name = "data_collection"
    # STOP G M1 显式声明（禁止隐式默认）：采集报告 Markdown 给 LLM 阅读，text 型。
    output_type = "text"

    @property
    def _tool_fn(self):
        return data_collection_tool


async def data_collection_skill_node(state: dict) -> dict:
    """LangGraph 节点适配器 — 由 Supervisor 路由到此节点"""
    skill = DataCollectionSkill()
    cap = (
        state.get("plan", {})
        .get("nodes", {})
        .get(state.get("current_step_id", ""), {})
        .get("capability", "data.collect")
    )
    logger.info(f"[DataCollection Skill] cap={cap} step={state.get('current_step_id')}")
    return await skill.execute(state, step_capability=cap)
