"""
data_collection/skill.py — DataCollectionSkill

遵循项目 BaseSkill 规范:
  - capabilities: ["data.collect"]
  - _tool_fn → data_collection_tool
  - execute() 由 BaseSkill 提供（带重试/超时/告警）
"""

from multi_agent.skills.base import BaseSkill
from data_collection.tool import data_collection_tool
from utils.logger import logger


class DataCollectionSkill(BaseSkill):
    """数据采集 Skill — 从外部数据源采集、清洗、分析并写入数据库"""

    name = "data_collection"
    capabilities = ["data.collect"]

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
