"""skills/data_export/skill.py — Data Export Skill. Capability: data.export"""
from backend.orchestration.tools import export_csv_tool
from backend.orchestration.skills.base import BaseSkill
from backend.shared.logger import logger


class DataExportSkill(BaseSkill):
    name = "data_export"
    capabilities = ["data.export"]

    @property
    def _tool_fn(self):
        return export_csv_tool


async def data_export_skill_node(state: dict) -> dict:
    skill = DataExportSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(
        state.get("current_step_id", ""), {}).get("capability", "data.export")
    logger.info(f"[DataExport] step={state.get('current_step_id')}")
    return await skill.execute(state, step_capability=cap)
