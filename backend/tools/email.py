"""邮件工具 — 发送（SMTP | Agently Mail 双引擎）+ 收/搜/读/监听（批次1）。

发送引擎由 EMAIL_ENGINE 配置切换：
  smtp    — 传统 SMTP（原路径，幂等指纹 + 审批门不变）
  agently — QQ 邮箱 Agently Mail（agently-cli）；审批门 ensure_approved
            通过后以 --confirmed 直发（不叠加 CLI 自己的两阶段确认）
search / read / watch 三个只读能力仅 agently 引擎提供。
"""
import hashlib
import ssl
import time

from langchain_core.tools import tool
from backend.shared.logger import logger
from backend.shared.text_split import split_list

# ── 发送幂等：同指纹（收件人+主题+正文哈希）在窗口内只发一次。
# 背景: BaseSkill 用 to_thread+wait_for 执行 Tool，超时判重试时线程不可
# 取消——若 SMTP 实际已发出而 Skill 层判超时重试，会重复发信。
# 只缓存"已成功发出"的指纹；SMTP 异常不缓存，重试路径保持畅通。
_EMAIL_DEDUP_WINDOW_SECONDS = 600
_SENT_FINGERPRINTS: dict[str, float] = {}


def _email_fingerprint(
    to_list: list[str], cc_list: list[str], subject: str, body: str,
) -> str:
    """指纹基于**解析规整后**的收件人列表（排序 + 小写）。

    此前直接哈希原始字符串，收件人格式差异（多空格 / 大小写 /
    中文逗号 vs 英文逗号）会换指纹绕过重复发送拦截——对外发信
    是信誉面风险，故 2026-09-21 收口为规整后再哈希。
    """
    norm_to = ",".join(sorted(a.lower() for a in to_list))
    norm_cc = ",".join(sorted(a.lower() for a in cc_list))
    return hashlib.sha256(
        f"{norm_to}|{norm_cc}|{subject}|{body}".encode("utf-8")).hexdigest()


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
        # Phase3 STOP C（E1 收口）：发送幂等权威从 run_idempotent_operation
        # （Redis claim + PG result store——60s lease 过期后同 key 重新 NEW，
        # crash window 内可重复发信）升级为 run_idempotent_side_effect
        # （PG durable ledger：claim/owner CAS/UNCERTAIN 保守阻断）。
        # Redis 进程内指纹仅保留短时并发抑制职责，不再承担 correctness。
        from backend.infra.llm.quota import enforce_side_effect_budget
        from backend.shared.idempotency import run_idempotent_side_effect

        tenant_id = get_tool_tenant_id()
        actor_id = get_tool_user_id() or ""
        client_key = (idempotency_key or get_tool_idempotency_key()
                      or _stable_payload_key(payload))
        result = run_idempotent_side_effect(
            "email.send",
            payload,
            lambda: {"message": _send_email_effect(to, subject, body, cc)},
            tenant_id=tenant_id,
            actor_id=actor_id,
            client_key=client_key,
            lease_seconds=300,
            pre_execute=lambda: enforce_side_effect_budget(
                user_id=actor_id, tenant_id=tenant_id),
        )
        return str(result["message"])

    # Phase3 STOP C（E2 封死）：无可信租户上下文的直接发信路径已移除。
    # 原「兼容尚未经网关注入租户的旧直调/本地开发路径」绕过 durable 幂等
    # 与 provider 契约，构成未登记的外部写旁路——按 fail-closed 拒绝。
    # 任务运行时链路由 agent_tasks._bind_task_identity 保证身份存在；
    # HTTP 链路由网关注入租户头；无身份即不允许产生对外副作用。
    from backend.shared.idempotency import IdempotencyContextMissing

    raise IdempotencyContextMissing(
        "缺少可信租户上下文，拒绝发送邮件（STOP C：无身份外部写旁路已封死）")


def _stable_payload_key(payload: dict) -> str:
    """无客户端幂等键时的稳定 logical key：同内容同收件人 = 同一 logical effect。"""
    from backend.shared.idempotency import canonical_fingerprint

    return canonical_fingerprint(payload)


def _send_email_effect(to: str, subject: str, body: str, cc: str) -> str:
    """副作用边界（PG ledger 的 operation() 内执行）。

    返回消息字符串 = SUCCEEDED（executor complete）；
    NOT_SENT（明确未越过 SMTP 边界：连接/TLS/认证失败、服务器明确拒收）
        → ProviderEffectError：ledger 落 FAILED，可安全接管重试；
    UNKNOWN（DATA 阶段中断：sendmail 已写出但无确定结果）
        → SideEffectOutcomeUnknown：ledger 落 IDEMPOTENCY_UNCERTAIN，
        同 key 永久保守阻断——Redis 任何窗口过期都不能重新打开发信权限。
    """
    from backend.config import EMAIL_ENGINE

    if EMAIL_ENGINE == "agently":
        return _send_via_agently(to, subject, body, cc)
    return _send_via_smtp(to, subject, body, cc)


def _send_via_smtp(to: str, subject: str, body: str, cc: str) -> str:
    """SMTP 引擎发送（副作用边界分类版，STOP C）。"""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from backend.config import (
        SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM,
    )
    from backend.shared.idempotency import SideEffectOutcomeUnknown
    from backend.shared.provider_idempotency import ProviderEffectError

    to_list = split_list(to)
    cc_list = split_list(cc) if cc else []
    if not to_list:
        raise ProviderEffectError(
            f"收件人解析为空，请检查 to 参数（当前值: {to!r}）")

    # 进程内指纹窗口（短时并发抑制；correctness 在 PG ledger）
    fingerprint = _email_fingerprint(to_list, cc_list, subject, body)
    if _dup_blocked(fingerprint):
        return (f"[EMAIL DUPLICATE] 内容相同的邮件已发送成功（收件人 {to}，"
                f"主题 '{subject}'），为避免重复发送本次已拦截，请勿重试。")

    msg = MIMEMultipart("alternative")
    msg["From"] = SMTP_FROM
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg.attach(MIMEText(body, "html" if body.startswith("<") else "plain", "utf-8"))

    # ── 阶段 1：连接/TLS/认证——明确未越过副作用边界（NOT_SENT）──
    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15)
        # TLS 证书校验（2026-09-23 D1-3）：显式传系统 CA 默认 context
        server.starttls(context=ssl.create_default_context())
        server.login(SMTP_USER, SMTP_PASSWORD)
    except Exception as e:
        logger.error(f"[Tool:send_email] 连接/认证阶段失败（未发送）：{e}")
        raise ProviderEffectError(f"SMTP 连接/认证失败（未发送）：{e}") from e

    # ── 阶段 2：DATA 传输——副作用边界 ──
    try:
        server.sendmail(SMTP_FROM, to_list + cc_list, msg.as_string())
    except (smtplib.SMTPRecipientsRefused,
            smtplib.SMTPSenderRefused) as e:
        # 服务器明确拒收：消息未被接受（NOT_SENT，按拒绝处理可安全重试）
        logger.error(f"[Tool:send_email] 服务器明确拒收（未发送）：{e}")
        raise ProviderEffectError(f"SMTP 明确拒收（未发送）：{e}") from e
    except Exception as e:
        # DATA 已写出但无确定结果：服务器可能已接受消息 → UNKNOWN → IN_DOUBT
        logger.error(f"[Tool:send_email] DATA 阶段中断（结果未知）：{e}")
        raise SideEffectOutcomeUnknown(
            f"SMTP DATA 阶段中断，服务器可能已接受消息（结果未知）：{e}") from e
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001 — 会话收尾失败不影响结果判定
            pass

    _mark_sent(fingerprint)
    logger.info(f"[Tool:send_email] 已发送 → {to} ({subject})")
    return f"邮件已发送: 收件人 {to}, 主题 '{subject}'"


def _dup_blocked(fingerprint: str) -> bool:
    """进程内指纹窗口检查（短时并发抑制，非 correctness）。

    key 含租户前缀：跨租户同内容是不同 logical effect，不得互相抑制
    （否则 tenant B 的邮件会被 tenant A 的窗口拦下，造成静默丟发）。
    """
    from backend.tools.session import get_tool_tenant_id

    tenant = get_tool_tenant_id() or ""
    now = time.time()
    for fp, ts in list(_SENT_FINGERPRINTS.items()):
        if now - ts > _EMAIL_DEDUP_WINDOW_SECONDS:
            _SENT_FINGERPRINTS.pop(fp, None)
    return f"{tenant}:{fingerprint}" in _SENT_FINGERPRINTS


def _mark_sent(fingerprint: str) -> None:
    from backend.tools.session import get_tool_tenant_id

    tenant = get_tool_tenant_id() or ""
    _SENT_FINGERPRINTS[f"{tenant}:{fingerprint}"] = time.time()


def _send_via_agently(to: str, subject: str, body: str, cc: str) -> str:
    """Agently 引擎发送：审批门已过（ensure_approved），--confirmed 直发。

    STOP C：作为 ledger 的 operation() 执行——命令级失败（CLI 非零退出并
    返回 [AGENTLY ERROR:*]）是其"未完成发送"的权威报告，按 NOT_SENT 抛
    ProviderEffectError（FAILED 可重试）；进程崩溃窗口由 ledger 覆盖
    （fn 未返回 → running → 过期保守阻断）。
    """
    from backend.tools import agently
    from backend.shared.provider_idempotency import ProviderEffectError

    if not agently.agently_available():
        raise ProviderEffectError(
            "[AGENTLY ERROR:4] agently-cli 未安装，无法以 agently 引擎发送")

    # 收件人规整：与 SMTP 路径同口径（split_list + 指纹基于规整列表）
    to_list = split_list(to)
    cc_list = split_list(cc) if cc else []
    if not to_list:
        raise ProviderEffectError(
            f"收件人解析为空，请检查 to 参数（当前值: {to!r}）")

    fingerprint = _email_fingerprint(to_list, cc_list, subject, body)
    if _dup_blocked(fingerprint):
        return (f"[EMAIL DUPLICATE] 内容相同的邮件已发送成功（收件人 {to}，"
                f"主题 '{subject}'），为避免重复发送本次已拦截，请勿重试。")

    result = agently.agently_send(
        to=to_list, subject=subject, body=body, cc=cc_list or None,
    )
    if result.startswith("[AGENTLY ERROR:"):
        # CLI 明确报告发送失败 = 未完成发送（NOT_SENT），失败不缓存指纹，
        # ledger 落 FAILED，重试路径畅通
        logger.error(f"[Tool:send_email/agently] 发送失败：{result}")
        raise ProviderEffectError(f"Agently 发送失败：{result}")
    _mark_sent(fingerprint)
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
