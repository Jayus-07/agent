"""
skills/email/skill.py — Email Skill

Capability: email.send — 发送邮件（SMTP）
"""
from backend.orchestration.tools import send_email_tool
from backend.skills.base import BaseSkill
from backend.shared.logger import logger


class EmailSkill(BaseSkill):
    """邮件发送 Skill"""

    name = "email"
    capabilities = ["email.send"]
    description = "通过 SMTP 发送邮件。必须在报告/数据生成完成后再调用（依赖前序步骤的输出）。"
    params_schema = {
        "to": "收件人邮箱，多个用逗号分隔",
        "subject": "邮件主题",
        "body": "邮件正文（支持 Markdown/HTML）",
        "cc": "抄送邮箱（可选）",
    }
    examples = [{"to": "team@company.com", "subject": "运营周报", "body": "# 本周运营数据\n\n..."}]

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
