"""
skills/email/skill.py — Email Skill（批次1 升级：Agently Mail 接入）

Capabilities:
  email.send   — 发送邮件（引擎由 EMAIL_ENGINE 切换：smtp | agently）
  email.search — 搜索邮件（仅 agently 引擎）
  email.read   — 读取邮件完整内容（仅 agently 引擎）
  email.watch  — 监听新邮件长轮询（仅 agently 引擎；内部能力，供通知闭环消费）

发送写操作仍走工具层 ensure_approved 审批门；agently 引擎在审批通过后
以 --confirmed 直发，不叠加 CLI 自己的两阶段确认。
"""
from backend.orchestration.tools import send_email_tool
from backend.tools.email import read_email_tool, search_email_tool, watch_email_tool
from backend.skills.base import BaseSkill
from backend.shared.logger import logger


class EmailSkill(BaseSkill):
    """邮件 Skill（发送 + 收/搜/读/监听）"""

    name = "email"

    @property
    def _tool_fn(self):
        return send_email_tool

    def _select_tool(self, capability: str, params: dict):
        """按 capability/action 分发到单职责 Tool，参数过滤到目标签名内
        （参照 CompetitorAnalysisSkill 的分发模式）。"""
        action = params.get("action") or ""
        if action == "search" or capability == "email.search":
            tool = search_email_tool
        elif action == "read" or capability == "email.read":
            tool = read_email_tool
        elif action == "watch" or capability == "email.watch":
            tool = watch_email_tool
        else:
            tool = send_email_tool
        allowed = set(getattr(tool, "args", {}) or {})
        return tool, {k: v for k, v in params.items() if k in allowed}


async def email_skill_node(state: dict) -> dict:
    """LangGraph 节点适配器"""
    skill = EmailSkill()
    cap = state.get("plan", {}).get("nodes", {}).get(
        state.get("current_step_id", ""), {}).get("capability", "email.send")
    logger.info(f"[Email Skill] cap={cap} step={state.get('current_step_id')}")
    return await skill.execute(state, step_capability=cap)
