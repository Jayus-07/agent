"""skills/email — Email Skill 注册"""
from backend.orchestration.tool_registry import tool_registry
from backend.orchestration.skills.email.skill import email_skill_node

tool_registry.register_skill_node("email_skill", email_skill_node)
