"""skills/web_search/skill.py — Web Search Skill. Capability: web.search"""
from backend.orchestration.tools import web_search_tool
from backend.skills.base import BaseSkill
from backend.shared.logger import logger


class WebSearchSkill(BaseSkill):
    name = "web_search"
    capabilities = ["web.search"]
    description = "搜索外部网页，补充知识库未覆盖的最新信息（市场动态、竞品信息、行业趋势等）。仅在内部知识库无法回答时使用。"
    params_schema = {
        "query": "搜索关键词",
        "num_results": "返回结果数（默认5）",
    }
    examples = [{"query": "Amazon FBA fee changes 2026"}]

    @property
    def _tool_fn(self):
        return web_search_tool


async def web_search_skill_node(state: dict) -> dict:
    skill = WebSearchSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(
        state.get("current_step_id", ""), {}).get("capability", "web.search")
    logger.info(f"[WebSearch] step={state.get('current_step_id')}")
    return await skill.execute(state, step_capability=cap)
