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
    capabilities = ["email.send", "email.search", "email.read", "email.watch"]
    description = (
        "邮件能力：发送邮件（SMTP 或 Agently 引擎）；搜索、读取、监听 Agently "
        "邮箱（EMAIL_ENGINE=agently 且完成 OAuth 后可用）。发送必须在报告/数据"
        "生成完成后再调用（依赖前序步骤的输出）。"
    )
    params_schema = {
        "action": {
            "type": "string", "required": False,
            "enum": ["send", "search", "read", "watch"],
            "description": "操作类型（默认 send）",
        },
        "to": {"type": "string", "required": False, "description": "收件人邮箱，多个用逗号分隔（send 必填）"},
        "subject": {"type": "string", "required": False, "description": "邮件主题（send 必填）"},
        "body": {"type": "string", "required": False, "description": "邮件正文（支持 Markdown/HTML）（send 必填）"},
        "cc": {"type": "string", "required": False, "description": "抄送邮箱"},
        "idempotency_key": {"type": "string", "required": False,
                            "description": "客户端幂等键；重复提交同键不会重复发信"},
        "query": {"type": "string", "required": False, "description": "搜索关键词（search 必填）"},
        "folder": {"type": "string", "required": False, "description": "文件夹 inbox/sent/trash/spam（search 可选）"},
        "message_id": {"type": "string", "required": False, "description": "邮件 ID，形如 msg_xxx（read 必填）"},
        "limit": {"type": "int", "required": False, "description": "返回条数（search 默认 10）"},
        "timeout_sec": {"type": "int", "required": False, "description": "watch 等待窗口秒数（默认 120）"},
    }
    examples = [
        {"action": "send", "to": "team@company.com", "subject": "运营周报", "body": "# 本周运营数据\n\n..."},
        {"action": "search", "query": "发货单", "folder": "inbox"},
        {"action": "read", "message_id": "msg_xxx"},
        {"action": "watch", "timeout_sec": 60},
    ]

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
