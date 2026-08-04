"""
skills/report/skill.py — Report Skill

Capability: report.generate — 数据 + Jinja2 模板 → Markdown 报告（含图表）
"""

from backend.orchestration.tools import generate_report_tool
from backend.orchestration.skills.base import BaseSkill
from backend.shared.logger import logger


class ReportSkill(BaseSkill):
    """报告生成 Skill"""

    name = "report"
    capabilities = ["report.generate"]
    description = "生成结构化 Markdown 报告（含图表），基于数据库中的实时数据。必须有前序步骤提供数据后再调用。"
    params_schema = {
        "report_type": (
            "报告类型，可选值：\n"
            "  - daily_sales: 销售日报\n"
            "  - product_performance: 商品动销分析\n"
            "  - inventory_health: 库存健康报告\n"
            "  - ad_performance: 广告效果分析\n"
            "  - order_fulfillment: 订单履约报告\n"
            "  - customer_analysis: 客户分析报告"
        ),
        "filters": "筛选条件字典，如 {'channel': 'Amazon'}",
    }
    examples = [{"report_type": "daily_sales", "filters": {"channel": "Amazon"}}]

    @property
    def _tool_fn(self):
        return generate_report_tool


async def report_skill_node(state: dict) -> dict:
    """LangGraph 节点适配器"""
    skill = ReportSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(state.get("current_step_id", ""), {}).get("capability", "report.generate")
    logger.info(f"[Report Skill] cap={cap} step={state.get('current_step_id')}")
    return await skill.execute(state, step_capability=cap)
