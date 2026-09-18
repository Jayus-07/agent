"""邮件工具 — 发送（SMTP | Agently Mail 双引擎）+ 收/搜/读/监听（批次1）。

发送引擎由 EMAIL_ENGINE 配置切换：
  smtp    — 传统 SMTP（原路径，幂等指纹 + 审批门不变）
  agently — QQ 邮箱 Agently Mail（agently-cli）；审批门 ensure_approved
            通过后以 --confirmed 直发（不叠加 CLI 自己的两阶段确认）
search / read / watch 三个只读能力仅 agently 引擎提供。
"""
import hashlib
import time

from langchain_core.tools import tool
from backend.shared.logger import logger

# ── 发送幂等：同指纹（收件人+主题+正文哈希）在窗口内只发一次。
# 背景: BaseSkill 用 to_thread+wait_for 执行 Tool，超时判重试时线程不可
# 取消——若 SMTP 实际已发出而 Skill 层判超时重试，会重复发信。
# 只缓存"已成功发出"的指纹；SMTP 异常不缓存，重试路径保持畅通。
_EMAIL_DEDUP_WINDOW_SECONDS = 600
_SENT_FINGERPRINTS: dict[str, float] = {}


def _email_fingerprint(to: str, cc: str, subject: str, body: str) -> str:
    return hashlib.sha256(
        f"{to}|{cc or ''}|{subject}|{body}".encode("utf-8")).hexdigest()


@tool
def send_email_tool(to: str, subject: str, body: str, cc: str = "",
                    idempotency_key: str = "") -> str:
    """
    发送邮件（写操作，首次执行需管理员审批）。
    to: 收件人邮箱，多个用逗号分隔
    subject: 邮件主题
    body: 邮件正文（支持 Markdown）
    cc: 抄送（可选）
    idempotency_key: 客户端幂等键（可选）
    """
    from backend.config import (
        SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, EMAIL_ENGINE,
    )
    from backend.security.tool_approval import ensure_approved
    from backend.tools.session import (
        get_tool_idempotency_key, get_tool_tenant_id, get_tool_user_id,
    )

    # 2026-09-17 B6 修复：SMTP 凭据检查只对 smtp 引擎生效。原实现无差别
    # 检查，agently 引擎（不依赖 SMTP 凭据）在未配 SMTP 的环境被
    # [EMAIL DISABLED] 拦截，永远走不到 agently 发送分支——B6 发送侧
    # 验收因此从未发生。
    if EMAIL_ENGINE != "agently" and (not SMTP_USER or not SMTP_PASSWORD):
        return f"[EMAIL DISABLED] 未配置 SMTP。收件人: {to}, 主题: {subject}, 正文长度: {len(body)} 字符"

    # 写操作审批门：detail 只含稳定字段（body 用哈希），保证批准后重试命中同指纹
    pending = ensure_approved(
        "send_email", "send",
        user_id=get_tool_user_id(),
        detail={"to": to, "cc": cc, "subject": subject,
                "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()},
    )
    if pending is not None:
        return pending

    payload = {"to": to, "cc": cc, "subject": subject, "body": body}
    if get_tool_tenant_id():
        from backend.shared.idempotency import run_idempotent_operation

        result = run_idempotent_operation(
            "email.send",
            payload,
            lambda: {"message": _send_email_after_approval(to, subject, body, cc)},
            client_key=idempotency_key or get_tool_idempotency_key(),
        )
        return str(result["message"])

    # 兼容尚未经网关注入租户的旧直调/本地开发路径；一旦有可信租户，
    # 必须走 Redis claim + PG 结果存储，Redis 故障不得降级放行。
    return _send_email_after_approval(to, subject, body, cc)


def _send_email_after_approval(to: str, subject: str, body: str, cc: str) -> str:
    """审批通过后的实际发信；全局幂等 claim 在调用此函数之前完成。"""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from backend.config import (
        SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM, EMAIL_ENGINE,
    )

    if EMAIL_ENGINE == "agently":
        return _send_via_agently(to, subject, body, cc)

    # 幂等拦截：窗口内同指纹视为重试，直接拒绝再次发送
    now = time.time()
    for fp, ts in list(_SENT_FINGERPRINTS.items()):
        if now - ts > _EMAIL_DEDUP_WINDOW_SECONDS:
            _SENT_FINGERPRINTS.pop(fp, None)
    fingerprint = _email_fingerprint(to, cc, subject, body)
    if fingerprint in _SENT_FINGERPRINTS:
        logger.warning(f"[Tool:send_email] 幂等拦截: 窗口内已发送过 → {to} ({subject})")
        return (f"[EMAIL DUPLICATE] 内容相同的邮件已发送成功（收件人 {to}，"
                f"主题 '{subject}'），为避免重复发送本次已拦截，请勿重试。")

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

        _SENT_FINGERPRINTS[fingerprint] = time.time()
        logger.info(f"[Tool:send_email] 已发送 → {to} ({subject})")
        return f"邮件已发送: 收件人 {to}, 主题 '{subject}'"
    except Exception as e:
        logger.error(f"[Tool:send_email] 发送失败：{e}")
        raise


def _send_via_agently(to: str, subject: str, body: str, cc: str) -> str:
    """Agently 引擎发送：审批门已过（ensure_approved），--confirmed 直发。

    幂等指纹与 SMTP 路径共用同一份 _SENT_FINGERPRINTS（收件人+主题+正文
    哈希同口径），引擎切换不会造成窗口内重复发送。
    """
    from backend.tools import agently

    if not agently.agently_available():
        return "[AGENTLY ERROR:4] agently-cli 未安装，无法以 agently 引擎发送"

    fingerprint = _email_fingerprint(to, cc, subject, body)
    if fingerprint in _SENT_FINGERPRINTS:
        return (f"[EMAIL DUPLICATE] 内容相同的邮件已发送成功（收件人 {to}，"
                f"主题 '{subject}'），为避免重复发送本次已拦截，请勿重试。")

    cc_list = [a.strip() for a in cc.split(",") if a.strip()] if cc else []
    result = agently.agently_send(
        to=[a.strip() for a in to.split(",") if a.strip()],
        subject=subject, body=body, cc=cc_list or None,
    )
    if result.startswith("[AGENTLY ERROR:"):
        # 与 SMTP 路径一致：失败不缓存指纹，重试路径畅通
        logger.error(f"[Tool:send_email/agently] 发送失败：{result}")
        return result
    _SENT_FINGERPRINTS[fingerprint] = time.time()
    logger.info(f"[Tool:send_email/agently] 已发送 → {to} ({subject})")
    return f"邮件已发送(Agently): 收件人 {to}, 主题 '{subject}'"


# ==================== 只读能力（仅 agently 引擎，批次1） ====================

@tool
def search_email_tool(query: str, folder: str = "", limit: int = 10,
                      cursor: str = "") -> str:
    """
    搜索 Agently 邮箱邮件（只读，需 EMAIL_ENGINE=agently 且已完成 OAuth）。
    query: 关键词
    folder: 文件夹 inbox/sent/trash/spam（可选，默认全部）
    limit: 返回条数（默认 10）
    cursor: 翻页游标（必须保留原查询条件再传 cursor）
    """
    from backend.tools import agently
    if not agently.agently_available():
        return "[AGENTLY ERROR:4] agently-cli 未安装，搜索不可用"
    return agently.agently_search(query=query, folder=folder, limit=limit, cursor=cursor)


@tool
def read_email_tool(message_id: str) -> str:
    """
    读取 Agently 邮件完整内容（只读，含正文与附件清单）。
    message_id: 邮件 ID，形如 msg_xxx（来自 search/list 结果）
    """
    from backend.tools import agently
    if not agently.agently_available():
        return "[AGENTLY ERROR:4] agently-cli 未安装，读取不可用"
    return agently.agently_read(message_id=message_id)


@tool
def watch_email_tool(timeout_sec: int = 120) -> str:
    """
    监听 Agently 新邮件（长轮询，窗口内无新邮件返回空结果）。
    timeout_sec: 等待窗口秒数（上限 AGENTLY_WATCH_MAX_SECONDS，默认 120）。
    供通知闭环/automation 消费，不建议在对话中长时间挂起。
    """
    from backend.tools import agently
    if not agently.agently_available():
        return "[AGENTLY ERROR:4] agently-cli 未安装，监听不可用"
    return agently.agently_watch(timeout_sec=float(timeout_sec))


# ==================== Tool Registry 自动注册 ====================
from backend.tools.tool_registry import tool_registry
tool_registry.register(send_email_tool, __file__)
tool_registry.register(search_email_tool, __file__)
tool_registry.register(read_email_tool, __file__)
tool_registry.register(watch_email_tool, __file__)
