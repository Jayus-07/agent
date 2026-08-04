"""skills/data_export/skill.py — Data Export Skill. Capability: data.export"""
from backend.orchestration.tools import export_csv_tool
from backend.skills.base import BaseSkill
from backend.shared.logger import logger


class DataExportSkill(BaseSkill):
    name = "data_export"
    capabilities = ["data.export"]
    description = "查询数据库并导出结果为 CSV 文件（UTF-8 BOM，Excel 可直接打开）。适用场景：导出报表、数据明细给业务团队。"
    params_schema = {
        "question": "自然语言查询问题（如 '上周各渠道销售额和订单数'）",
        "filename": "导出文件名（可选，不含扩展名）",
    }
    examples = [{"question": "上周各渠道销售额和订单数", "filename": "weekly_sales"}]

    @property
    def _tool_fn(self):
        return export_csv_tool


async def data_export_skill_node(state: dict) -> dict:
    skill = DataExportSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(
        state.get("current_step_id", ""), {}).get("capability", "data.export")
    logger.info(f"[DataExport] step={state.get('current_step_id')}")
    return await skill.execute(state, step_capability=cap)
