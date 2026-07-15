"""
skills/sql/skill.py — SQL Skill

Capability: sql.query — 自然语言 → SQL → 数据库查询结果
"""

from backend.agent.tools import sql_query_tool
from backend.agent.skills.base import BaseSkill
from backend.utils.logger import logger


class SQLSkill(BaseSkill):
    """数据库查询 Skill"""

    name = "sql"
    capabilities = ["sql.query"]

    @property
    def _tool_fn(self):
        return sql_query_tool


async def sql_skill_node(state: dict) -> dict:
    """LangGraph 节点适配器"""
    skill = SQLSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(state.get("current_step_id", ""), {}).get("capability", "sql.query")
    logger.info(f"[SQL Skill] cap={cap} step={state.get('current_step_id')}")
    return await skill.execute(state, step_capability=cap)
