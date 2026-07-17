"""skills/web_search/skill.py — Web Search Skill. Capability: web.search"""
from backend.orchestration.tools import web_search_tool
from backend.orchestration.skills.base import BaseSkill
from backend.shared.logger import logger


class WebSearchSkill(BaseSkill):
    name = "web_search"
    capabilities = ["web.search"]

    @property
    def _tool_fn(self):
        return web_search_tool


async def web_search_skill_node(state: dict) -> dict:
    skill = WebSearchSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(
        state.get("current_step_id", ""), {}).get("capability", "web.search")
    logger.info(f"[WebSearch] step={state.get('current_step_id')}")
    return await skill.execute(state, step_capability=cap)
