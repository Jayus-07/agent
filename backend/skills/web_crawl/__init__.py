"""skills/web_crawl — Web Crawl Skill 注册"""
from backend.orchestration.tool_registry import tool_registry
from backend.skills.web_crawl.skill import web_crawl_skill_node

tool_registry.register_skill_node("web_crawl_skill", web_crawl_skill_node)
