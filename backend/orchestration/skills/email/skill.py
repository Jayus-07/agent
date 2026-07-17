"""
skills/email/skill.py — Email Skill

Capability: email.send — 发送邮件（SMTP）
"""
from backend.orchestration.tools import send_email_tool
from backend.orchestration.skills.base import BaseSkill
from backend.shared.logger import logger


class EmailSkill(BaseSkill):
    """邮件发送 Skill"""

    name = "email"
    capabilities = ["email.send"]

    @property
    def _tool_fn(self):
        return send_email_tool


async def email_skill_node(state: dict) -> dict:
    """LangGraph 节点适配器"""
    skill = EmailSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(
        state.get("current_step_id", ""), {}).get("capability", "email.send")
    logger.info(f"[Email Skill] step={state.get('current_step_id')}")
    return await skill.execute(state, step_capability=cap)
