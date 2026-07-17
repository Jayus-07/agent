"""skills/web_search — Web Search Skill 注册"""
from backend.orchestration.tool_registry import tool_registry
from backend.orchestration.skills.web_search.skill import web_search_skill_node

tool_registry.register_skill_node("web_search_skill", web_search_skill_node)
