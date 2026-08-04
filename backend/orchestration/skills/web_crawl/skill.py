"""skills/web_crawl/skill.py — Web Crawl Skill. Capability: web.crawl"""
from backend.orchestration.tools import web_crawl_tool
from backend.orchestration.skills.base import BaseSkill
from backend.shared.logger import logger


class WebCrawlSkill(BaseSkill):
    name = "web_crawl"
    capabilities = ["web.crawl"]
    description = "抓取指定网页的正文内容，输出干净 Markdown。用于获取竞品页面详情、行业资讯全文、平台政策原文。建议先通过 web.search 发现目标链接后再调用本能力。"
    params_schema = {
        "url": "目标网页 URL（完整地址）",
        "mode": "markdown（默认，干净正文）| raw（原始 HTML）",
    }
    examples = [{"url": "https://www.amazon.com/dp/B0EXAMPLE", "mode": "markdown"}]

    @property
    def _tool_fn(self):
        return web_crawl_tool


async def web_crawl_skill_node(state: dict) -> dict:
    skill = WebCrawlSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(
        state.get("current_step_id", ""), {}).get("capability", "web.crawl")
    logger.info(f"[WebCrawl] step={state.get('current_step_id')}")
    return await skill.execute(state, step_capability=cap)
