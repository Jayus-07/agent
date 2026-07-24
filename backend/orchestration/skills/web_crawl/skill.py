"""skills/web_crawl/skill.py — Web Crawl Skill. Capability: web.crawl"""
from backend.orchestration.tools import web_crawl_tool
from backend.orchestration.skills.base import BaseSkill
from backend.shared.logger import logger


class WebCrawlSkill(BaseSkill):
    name = "web_crawl"
    capabilities = ["web.crawl"]

    @property
    def _tool_fn(self):
        return web_crawl_tool


async def web_crawl_skill_node(state: dict) -> dict:
    skill = WebCrawlSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(
        state.get("current_step_id", ""), {}).get("capability", "web.crawl")
    logger.info(f"[WebCrawl] step={state.get('current_step_id')}")
    return await skill.execute(state, step_capability=cap)
