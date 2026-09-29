"""
skills/report/skill.py — Report Skill

Capability: report.generate — 数据 + Jinja2 模板 → Markdown 报告（含图表）
"""

from backend.orchestration.tools import generate_report_tool
from backend.skills.base import BaseSkill
from backend.shared.logger import logger


class ReportSkill(BaseSkill):
    """报告生成 Skill"""

    name = "report"
    # STOP G M1 显式声明（禁止隐式默认）：Markdown 报告本体即交付物，text 型。
    output_type = "text"

    @property
    def _tool_fn(self):
        return generate_report_tool


async def report_skill_node(state: dict) -> dict:
    """LangGraph 节点适配器"""
    skill = ReportSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(state.get("current_step_id", ""), {}).get("capability", "report.generate")
    logger.info(f"[Report Skill] cap={cap} step={state.get('current_step_id')}")
    return await skill.execute(state, step_capability=cap)
