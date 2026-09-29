"""skills/web_crawl/skill.py — Web Crawl Skill. Capability: web.crawl"""
from backend.orchestration.tools import web_crawl_tool
from backend.skills.base import BaseSkill
from backend.shared.logger import logger


class WebCrawlSkill(BaseSkill):
    name = "web_crawl"
    # STOP G M1 显式声明（禁止隐式默认）：网页正文 Markdown/raw HTML 给 LLM 阅读，text 型。
    output_type = "text"

    @property
    def _tool_fn(self):
        return web_crawl_tool


async def web_crawl_skill_node(state: dict) -> dict:
    skill = WebCrawlSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(
        state.get("current_step_id", ""), {}).get("capability", "web.crawl")
    logger.info(f"[WebCrawl] step={state.get('current_step_id')}")
    return await skill.execute(state, step_capability=cap)
