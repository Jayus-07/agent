"""
skills/sql/skill.py — SQL Skill

Capability: sql.query — 自然语言 → SQL → 数据库查询结果
"""

from backend.orchestration.tools import sql_query_tool
from backend.orchestration.skills.base import BaseSkill
from backend.shared.logger import logger


class SQLSkill(BaseSkill):
    """数据库查询 Skill"""

    name = "sql"
    capabilities = ["sql.query"]
    description = "查询 PostgreSQL 跨境电商数据库，返回 Markdown 表格。覆盖商品/订单/库存/广告/物流/客户等 15 张表。"
    params_schema = {"question": "自然语言查询问题（中文/英文）"}
    examples = [{"question": "查询Amazon US渠道最近7天的销售额和订单数"}]

    @property
    def _tool_fn(self):
        return sql_query_tool


async def sql_skill_node(state: dict) -> dict:
    """LangGraph 节点适配器"""
    skill = SQLSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(state.get("current_step_id", ""), {}).get("capability", "sql.query")
    logger.info(f"[SQL Skill] cap={cap} step={state.get('current_step_id')}")
    return await skill.execute(state, step_capability=cap)
