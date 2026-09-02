"""邮件工具 — SMTP 发送。"""
from langchain_core.tools import tool
from backend.shared.logger import logger

@tool
def send_email_tool(to: str, subject: str, body: str, cc: str = "") -> str:
    """
    发送邮件。
    to: 收件人邮箱，多个用逗号分隔
    subject: 邮件主题
    body: 邮件正文（支持 Markdown）
    cc: 抄送（可选）
    """
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from backend.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM

    if not SMTP_USER or not SMTP_PASSWORD:
        return f"[EMAIL DISABLED] 未配置 SMTP。收件人: {to}, 主题: {subject}, 正文长度: {len(body)} 字符"

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = SMTP_FROM
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        msg.attach(MIMEText(body, "html" if body.startswith("<") else "plain", "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            recipients = [a.strip() for a in to.split(",")]
            if cc:
                recipients += [a.strip() for a in cc.split(",")]
            server.sendmail(SMTP_FROM, recipients, msg.as_string())

        logger.info(f"[Tool:send_email] 已发送 → {to} ({subject})")
        return f"邮件已发送: 收件人 {to}, 主题 '{subject}'"
    except Exception as e:
        logger.error(f"[Tool:send_email] 发送失败：{e}")
        raise


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry
tool_registry.register(send_email_tool, __file__)
