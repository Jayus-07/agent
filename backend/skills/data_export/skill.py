"""skills/data_export/skill.py — Data Export Skill. Capability: data.export"""
from backend.orchestration.tools import export_csv_tool
from backend.skills.base import BaseSkill
from backend.shared.logger import logger


class DataExportSkill(BaseSkill):
    name = "data_export"
    # STOP G M1 显式声明（禁止隐式默认）：导出结果为文本回执（路径/失败标记），text 型。
    output_type = "text"

    @property
    def _tool_fn(self):
        return export_csv_tool


async def data_export_skill_node(state: dict) -> dict:
    skill = DataExportSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(
        state.get("current_step_id", ""), {}).get("capability", "data.export")
    logger.info(f"[DataExport] step={state.get('current_step_id')}")
    return await skill.execute(state, step_capability=cap)
