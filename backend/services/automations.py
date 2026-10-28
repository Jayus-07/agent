"""services/automations.py — 用户自动任务持久层（决策 #27，agent_memory 库）。

职责：ai.user_automations / ai.automation_events 两张表的 CRUD + 触发扫描。
- 连接：psycopg3 直连 MEMORY_DB_CONFIG（与 task_service 同库同源）
- 隔离：用户维度查询强制 WHERE user_id = %s AND tenant_id = %s
  （user_id 一律取服务端验证后的当前身份，禁止请求体传入）
- 去重：ai.automation_events(automation_id, external_event_id) 部分唯一索引
- cron 匹配：自实现 5 字段简化匹配器（croniter 不在项目依赖里，全局规则
  禁止未要求的依赖；语法覆盖 * / 列表 区间，语义见 _match_field）

v1 边界（#27 决策，不可裁剪语义的实现位置）：
- confirm_policy 只允许 'auto'（写库前拒绝其他值）：自动执行按只读语义
  理解，payload 只允许纯问题文本 {"question": str}；写操作不因自动化
  绕过——既有 Tool Governance / 确认门在 tasks.execute_agent 执行链内照常生效；
- webhook 密钥照凭据中心模式（shared/crypto Fernet 密文落库），明文只在
  创建响应返回一次（回调方需要原密钥计算 HMAC 签名，哈希无法验签）。

注意：同步阻塞 IO。API 层经 FastAPI 线程池调用无碍；
Worker（Celery prefork）天然同步，直接调用。
"""
from __future__ import annotations

import hmac
import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from backend.config.database import DB_CONNECT_TIMEOUT, MEMORY_DB_CONFIG
from backend.shared.logger import logger

TRIGGER_TYPES = ("cron", "webhook")
# v1 只开放 auto：require_confirmation 词表预留，写入路径显式拒绝
ALLOWED_CONFIRM_POLICIES = ("auto",)
EVENT_TERMINAL_STATUSES = ("succeeded", "failed", "skipped_duplicate")


class AutomationValidationError(ValueError):
    """CRUD 入参不合法（API 层映射 400）。"""


# ═══════════════════════════════════════════════════
# 连接（照 task_service 同款：autocommit 短连接）
# ═══════════════════════════════════════════════════

def _dsn() -> str:
    c = MEMORY_DB_CONFIG
    return (f"postgresql://{c['user']}:{c['password']}"
            f"@{c['host']}:{c['port']}/{c['dbname']}")


def _conn():
    import psycopg

    return psycopg.connect(
        _dsn(),
        autocommit=True,
        connect_timeout=DB_CONNECT_TIMEOUT,
    )


# ═══════════════════════════════════════════════════
# 简化 cron 匹配器（5 字段：分 时 日 月 周，UTC 语义）
# ═══════════════════════════════════════════════════

_FIELD_RANGES = (
    (0, 59),   # minute
    (0, 23),   # hour
    (1, 31),   # day of month
    (1, 12),   # month
    (0, 6),    # day of week（0=周日；7 归一化为 0）
)


def parse_cron(expr: str) -> list[set[int]]:
    """5 字段 cron → 每字段允许值集合。非法表达式抛 AutomationValidationError。

    简化边界（注释声明）：不支持月份/星期名（JAN/SUN）、L/W/# 修饰符、
    秒级精度；语义按标准 cron（日/周同时非 * 时为 OR，见 cron_matches）。
    """
    text = (expr or "").strip()
    if not text:
        raise AutomationValidationError("cron 表达式不能为空")
    fields = text.split()
    if len(fields) != 5:
        raise AutomationValidationError(
            f"cron 表达式必须为 5 字段（分 时 日 月 周）: {text!r}")
    parsed: list[set[int]] = []
    for idx, (field, (lo, hi)) in enumerate(zip(fields, _FIELD_RANGES)):
        values: set[int] = set()
        for part in field.split(","):
            part = part.strip()
            if not part:
                raise AutomationValidationError(f"cron 字段 {idx} 含空列表项")
            step = 1
            body = part
            if "/" in part:
                body, _, step_s = part.partition("/")
                try:
                    step = int(step_s)
                except ValueError:
                    raise AutomationValidationError(
                        f"cron 字段 {idx} 步长非法: {part!r}") from None
                if step < 1:
                    raise AutomationValidationError(
                        f"cron 字段 {idx} 步长必须 >= 1: {part!r}")
            if body == "*":
                start, end = lo, hi
            elif "-" in body:
                a, _, b = body.partition("-")
                try:
                    start, end = int(a), int(b)
                except ValueError:
                    raise AutomationValidationError(
                        f"cron 字段 {idx} 区间非法: {part!r}") from None
            else:
                try:
                    start = end = int(body)
                except ValueError:
                    raise AutomationValidationError(
                        f"cron 字段 {idx} 值非法: {part!r}") from None
            if idx == 4:  # day of week：7 视为 0（周日）
                start = 0 if start == 7 else start
                end = 0 if end == 7 else end
            if not (lo <= start <= hi and lo <= end <= hi) or start > end:
                raise AutomationValidationError(
                    f"cron 字段 {idx} 取值越界（合法 {lo}-{hi}）: {part!r}")
            values.update(range(start, end + 1, step))
        if not values:
            raise AutomationValidationError(f"cron 字段 {idx} 为空")
        parsed.append(values)
    return parsed


def cron_matches(expr: str, moment: datetime) -> bool:
    """moment（UTC 语义的分钟值）是否命中 5 字段 cron。

    标准 cron 语义：day-of-month 与 day-of-week 同时受限（都非 *）时
    取 OR，仅一个受限时取 AND（vixie cron 惯例）。
    """
    values = parse_cron(expr)
    minute, hour, dom, month, dow = (
        moment.minute, moment.hour, moment.day, moment.month,
        (moment.weekday() + 1) % 7,  # Python 周一=0 → cron 0=周日
    )
    if minute not in values[0] or hour not in values[1] or month not in values[3]:
        return False
    dom_restricted = not _is_star(_field_text(expr, 2))
    dow_restricted = not _is_star(_field_text(expr, 4))
    if dom_restricted and dow_restricted:
        day_ok = dom in values[2] or dow in values[4]  # 双受限 → OR
    elif dom_restricted:
        day_ok = dom in values[2]
    elif dow_restricted:
        day_ok = dow in values[4]
    else:
        day_ok = True
    return day_ok


def _field_text(expr: str, idx: int) -> str:
    fields = (expr or "").split()
    return fields[idx] if len(fields) == 5 else "*"


def _is_star(field: str) -> bool:
    return field == "*" or field.startswith("*/")


def is_cron_due(expr: str, *, last_run_at: datetime | None,
                now: datetime) -> bool:
    """分钟级到期判定：本分钟窗口命中 cron 且该窗口尚未派发过。

    last_run_at 存的是对齐分钟窗口（cron 派发时写入），所以同分钟内
    beat 重复扫描天然不重复派发；跨分钟重扫由窗口推进自然放行。
    """
    window = now.replace(second=0, microsecond=0)
    if not cron_matches(expr, window):
        return False
    if last_run_at is None:
        return True
    if last_run_at.tzinfo is None:
        last_run_at = last_run_at.replace(tzinfo=timezone.utc)
    return last_run_at < window


# ═══════════════════════════════════════════════════
# 行序列化与校验
# ═══════════════════════════════════════════════════

_AUTOMATION_COLS = ("id, user_id, tenant_id, name, trigger_type, cron_expr, "
                    "payload, confirm_policy, enabled, last_run_at, "
                    "last_status, created_at, updated_at")

# 内部通道列（含密文）：webhook 验签路径专用，任何 API 响应不得序列化
_AUTOMATION_COLS_INTERNAL = _AUTOMATION_COLS + ", webhook_secret_cipher"


def _row_to_dict(row, *, internal: bool = False) -> dict:
    data = {
        "id": row[0],
        "user_id": row[1],
        "tenant_id": row[2],
        "name": row[3],
        "trigger_type": row[4],
        "cron_expr": row[5],
        "payload": row[6] if isinstance(row[6], dict)
        else json.loads(row[6] or "{}"),
        "confirm_policy": row[7],
        "enabled": row[8],
        "last_run_at": row[9].isoformat() if row[9] else None,
        "last_status": row[10],
        "created_at": row[11].isoformat() if row[11] else None,
        "updated_at": row[12].isoformat() if row[12] else None,
    }
    if internal:
        # 密文仅进程内透传（验签/派发），序列化出口必须 pop 掉
        data["webhook_secret_cipher"] = row[13] if len(row) > 13 else ""
    return data

_AUTOMATION_COLS = ("id, user_id, tenant_id, name, trigger_type, cron_expr, "
                    "payload, confirm_policy, enabled, last_run_at, "
                    "last_status, created_at, updated_at")


def validate_payload(payload: dict) -> str:
    """v1 payload 契约：{"question": 非空纯文本}。返回 question 文本。

    纯问题文本 = 自动执行只读语义的落地点：Agent 以该 question 走既有
    chat/main 管线，写操作由既有 Governance/确认门兜底（不因自动化绕过）。
    """
    if not isinstance(payload, dict):
        raise AutomationValidationError("payload 必须是对象")
    extra = set(payload.keys()) - {"question"}
    if extra:
        raise AutomationValidationError(
            f"v1 payload 仅支持 {{question}}，多余键: {sorted(extra)}")
    question = payload.get("question")
    if not isinstance(question, str) or not question.strip():
        raise AutomationValidationError("payload.question 必须为非空文本")
    if len(question) > 4000:
        raise AutomationValidationError("payload.question 过长（>4000）")
    return question.strip()


def validate_confirm_policy(confirm_policy: str) -> str:
    if confirm_policy not in ALLOWED_CONFIRM_POLICIES:
        raise AutomationValidationError(
            f"v1 仅支持 confirm_policy='auto'（'require_confirmation' 预留未开放）；"
            "写操作由既有 Governance/确认门兜底，不因自动化绕过")
    return confirm_policy


def generate_webhook_secret() -> str:
    """生成 256bit 签名密钥（明文只在创建响应返回一次）。"""
    return f"whsec_{secrets.token_hex(32)}"


def webhook_url(tenant_id: str, automation_id: str) -> str:
    """回调地址（租户段在路径上：租户绑定校验的「路径来源」）。"""
    return f"/webhooks/automations/{tenant_id}/{automation_id}"


# ═══════════════════════════════════════════════════
# 用户 CRUD（隔离强制）
# ═══════════════════════════════════════════════════

def create_automation(*, user_id: str, tenant_id: str, name: str,
                      trigger_type: str, cron_expr: str = "",
                      payload: dict | None = None,
                      confirm_policy: str = "auto") -> tuple[dict, str | None]:
    """创建自动任务。webhook 类型返回 (行, 明文密钥)；cron 类型密钥为 None。"""
    if not name.strip() or len(name) > 128:
        raise AutomationValidationError("name 必须为 1-128 字符")
    if trigger_type not in TRIGGER_TYPES:
        raise AutomationValidationError(
            f"trigger_type 必须是 {'/'.join(TRIGGER_TYPES)}")
    validate_confirm_policy(confirm_policy)
    question = validate_payload(payload or {})
    if trigger_type == "cron":
        parse_cron(cron_expr)  # 合法性校验（不匹配时刻只影响首次触发）
    automation_id = f"auto_{uuid.uuid4().hex}"
    secret = generate_webhook_secret() if trigger_type == "webhook" else None
    # 局部导入避免 API 进程启动期强依赖加密配置（SECRETS_ENCRYPTION_KEY 缺失
    # 只在真正创建 webhook 自动任务时报错，错误语义更聚焦）
    from backend.shared.crypto import encrypt_secret

    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO ai.user_automations
                (id, user_id, tenant_id, name, trigger_type, cron_expr,
                 webhook_secret_cipher, payload, confirm_policy, enabled)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
            """,
            (automation_id, user_id, tenant_id, name.strip(), trigger_type,
             cron_expr.strip() if trigger_type == "cron" else "",
             encrypt_secret(secret) if secret else "",
             json.dumps({"question": question}, ensure_ascii=False),
             confirm_policy),
        )
    return get_automation(automation_id, user_id=user_id,
                          tenant_id=tenant_id), secret


def list_automations(*, user_id: str, tenant_id: str) -> list[dict]:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {_AUTOMATION_COLS} FROM ai.user_automations "
            "WHERE user_id = %s AND tenant_id = %s ORDER BY created_at DESC",
            (user_id, tenant_id),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]


def get_automation(automation_id: str, *, user_id: str | None = None,
                   tenant_id: str | None = None,
                   internal: bool = False) -> dict | None:
    """读单行；user_id/tenant_id 提供时强制过滤（用户端隔离语义）。

    internal=True 时附带 webhook_secret_cipher（仅验签/派发路径使用）。
    """
    cols = _AUTOMATION_COLS_INTERNAL if internal else _AUTOMATION_COLS
    sql = f"SELECT {cols} FROM ai.user_automations WHERE id = %s"
    params: list = [automation_id]
    if user_id is not None:
        sql += " AND user_id = %s"
        params.append(user_id)
    if tenant_id is not None:
        sql += " AND tenant_id = %s"
        params.append(tenant_id)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return _row_to_dict(row, internal=internal) if row else None


def update_automation(automation_id: str, *, user_id: str, tenant_id: str,
                      name: str | None = None, cron_expr: str | None = None,
                      payload: dict | None = None,
                      enabled: bool | None = None) -> dict | None:
    """更新自己的行；None 字段不动。返回更新后的行（无权限/不存在 = None）。"""
    existing = get_automation(automation_id, user_id=user_id, tenant_id=tenant_id)
    if existing is None:
        return None
    sets: list[str] = ["updated_at = now()"]
    params: list = []
    if name is not None:
        if not name.strip() or len(name) > 128:
            raise AutomationValidationError("name 必须为 1-128 字符")
        sets.append("name = %s")
        params.append(name.strip())
    if cron_expr is not None:
        if existing["trigger_type"] != "cron":
            raise AutomationValidationError("webhook 类型自动任务不支持 cron_expr")
        parse_cron(cron_expr)
        sets.append("cron_expr = %s")
        params.append(cron_expr.strip())
    if payload is not None:
        question = validate_payload(payload)
        sets.append("payload = %s")
        params.append(json.dumps({"question": question}, ensure_ascii=False))
    if enabled is not None:
        sets.append("enabled = %s")
        params.append(bool(enabled))
    params.append(automation_id)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE ai.user_automations SET {', '.join(sets)} WHERE id = %s",
            params,
        )
    return get_automation(automation_id, user_id=user_id, tenant_id=tenant_id)


def delete_automation(automation_id: str, *, user_id: str,
                      tenant_id: str) -> bool:
    """删除自己的行（事件经 FK ON DELETE CASCADE 一并清除）。"""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM ai.user_automations "
            "WHERE id = %s AND user_id = %s AND tenant_id = %s",
            (automation_id, user_id, tenant_id),
        )
        return cur.rowcount > 0


def set_enabled(automation_id: str, *, enabled: bool,
                user_id: str | None = None,
                tenant_id: str | None = None) -> bool:
    """启停开关（用户端自用 + 管理端停用共用；过滤参数提供时强制隔离）。"""
    sql = "UPDATE ai.user_automations SET enabled = %s, updated_at = now() WHERE id = %s"
    params: list = [bool(enabled), automation_id]
    if user_id is not None:
        sql += " AND user_id = %s"
        params.append(user_id)
    if tenant_id is not None:
        sql += " AND tenant_id = %s"
        params.append(tenant_id)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount > 0


def set_last_status(automation_id: str, status: str,
                    *, touch_last_run: bool = False) -> None:
    """回写最近事件状态；touch_last_run=True 时同步刷新最近派发时刻。

    cron 路径的 last_run_at 由扫描认领写窗口值（对齐分钟），本函数的
    touch_last_run 供 webhook 路径使用；cron 成功回写会覆盖为实际完成
    时刻——恒大于窗口值，不破坏 is_cron_due 的同窗口去重语义。
    """
    sql = "UPDATE ai.user_automations SET last_status = %s, updated_at = now()"
    params: list = [status]
    if touch_last_run:
        sql += ", last_run_at = now()"
    sql += " WHERE id = %s"
    params.append(automation_id)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)


# ═══════════════════════════════════════════════════
# 事件账本（去重 + 终态）
# ═══════════════════════════════════════════════════

def record_event(automation_id: str, *, source: str,
                 external_event_id: str = "",
                 payload: dict | None = None,
                 status: str = "queued",
                 detail: str = "") -> int | None:
    """写入事件行（external_event_id 非空时唯一索引去重）。

    返回事件 id；去重命中（已存在同键行）返回 None——调用方按
    skipped_duplicate 语义短路，不重复派发。
    """
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ai.automation_events
                (automation_id, source, external_event_id, payload, status, detail)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (automation_id, external_event_id)
                WHERE external_event_id <> '' DO NOTHING
            RETURNING id
            """,
            (automation_id, source, external_event_id,
             json.dumps(payload or {}, ensure_ascii=False), status, detail),
        )
        row = cur.fetchone()
    return row[0] if row else None


def mark_event(event_id: int, *, status: str, detail: str = "",
               payload: dict | None = None) -> None:
    if status not in EVENT_TERMINAL_STATUSES and status != "running":
        raise ValueError(f"非法事件状态: {status}")
    sets = "status = %s, detail = %s"
    params: list = [status, detail]
    if payload is not None:
        sets += ", payload = %s"
        params.append(json.dumps(payload, ensure_ascii=False))
    params.append(event_id)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE ai.automation_events SET {sets} WHERE id = %s", params)


def list_events(automation_id: str, *, limit: int = 50) -> list[dict]:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, automation_id, source, external_event_id, payload,
                   status, detail, created_at
            FROM ai.automation_events WHERE automation_id = %s
            ORDER BY created_at DESC, id DESC LIMIT %s
            """,
            (automation_id, min(int(limit), 200)),
        )
        rows = cur.fetchall()
    return [
        {
            "id": r[0], "automation_id": r[1], "source": r[2],
            "external_event_id": r[3],
            "payload": r[4] if isinstance(r[4], dict)
            else json.loads(r[4] or "{}"),
            "status": r[5], "detail": r[6],
            "created_at": r[7].isoformat() if r[7] else None,
        }
        for r in rows
    ]


# ═══════════════════════════════════════════════════
# cron 扫描（beat 单实例调用；原子认领保多实例安全）
# ═══════════════════════════════════════════════════

def scan_due_cron_automations(now: datetime | None = None) -> list[dict]:
    """扫描到期的 enabled cron 自动任务 → 原子认领 + 事件行。

    认领语义：条件 UPDATE（last_run_at < 窗口）先原子占住分钟窗口，多实例
    并发扫描时只有一个胜出（beat 单实例下为理论兜底）；事件唯一索引
    （cron:<epoch_minute>）为第二道保险。返回已认领的
    {automation_id, user_id, tenant_id, event_id} 列表，派发由调用方
    （Celery 壳）执行——本函数不投递消息。
    """
    now = now or datetime.now(timezone.utc)
    window = now.replace(second=0, microsecond=0)
    claimed: list[dict] = []
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, user_id, tenant_id, cron_expr, last_run_at "
            "FROM ai.user_automations "
            "WHERE enabled AND trigger_type = 'cron'",
        )
        rows = cur.fetchall()
    for automation_id, user_id, tenant_id, cron_expr, last_run_at in rows:
        try:
            if not is_cron_due(cron_expr, last_run_at=last_run_at, now=now):
                continue
        except AutomationValidationError:
            # 表达式损坏（历史数据）：留日志不派发，不拖垮整轮扫描
            logger.error("[Automations] %s cron 表达式非法: %r",
                         automation_id, cron_expr)
            continue
        # 先原子认领窗口（认领即写 last_run_at=窗口）
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE ai.user_automations "
                "SET last_run_at = %s, last_status = 'queued', updated_at = now() "
                "WHERE id = %s AND enabled "
                "AND (last_run_at IS NULL OR last_run_at < %s)",
                (window, automation_id, window),
            )
            won = cur.rowcount > 0
        if not won:
            continue  # 并发已被认领（或刚被处理过）
        event_id = record_event(
            automation_id, source="cron",
            external_event_id=f"cron:{int(window.timestamp())}",
            payload={"window": window.isoformat()},
        )
        if event_id is None:
            # 唯一索引命中：该窗口已入账（认领与事件跨步异常中断的残留），
            # 不重复派发；日志留痕便于人工核对
            logger.warning("[Automations] %s 窗口 %s 事件已存在，跳过派发",
                           automation_id, window.isoformat())
            continue
        claimed.append({
            "automation_id": automation_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "event_id": event_id,
        })
    return claimed


# ═══════════════════════════════════════════════════
# Webhook 验签
# ═══════════════════════════════════════════════════

def verify_webhook_signature(secret: str, raw_body: bytes,
                             signature: str) -> bool:
    """HMAC-SHA256(raw_body, secret) hex 恒定时间比较。

    接受 hex（大小写不敏感）；不引入时间戳防重放——重放由 X-Event-Id
    去重兜底（同一事件 id 二次回调 → skipped_duplicate）。
    """
    if not secret or not signature:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, (signature or "").strip().lower())


def get_webhook_secret(automation: dict) -> str:
    """解密签名密钥（仅 webhook 类型；密文空 = 配置损坏，返回空串）。"""
    from backend.shared.crypto import SecretCryptoError, decrypt_secret

    cipher = automation.get("webhook_secret_cipher") or ""
    if not cipher:
        return ""
    try:
        return decrypt_secret(cipher)
    except SecretCryptoError:
        logger.error("[Automations] %s webhook 密钥解密失败（密钥轮换/损坏）",
                     automation.get("id"))
        return ""


# ═══════════════════════════════════════════════════
# 管理端总览（跨租户只读）
# ═══════════════════════════════════════════════════

def admin_overview() -> dict:
    """跨租户计数 + 最近失败事件（管理端总览与开关的数据源）。"""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE enabled),
                   count(*) FILTER (WHERE trigger_type = 'cron'),
                   count(*) FILTER (WHERE trigger_type = 'webhook')
            FROM ai.user_automations
            """
        )
        total, enabled, cron_n, webhook_n = cur.fetchone()
        cur.execute(
            """
            SELECT e.id, e.automation_id, a.name, a.tenant_id, a.user_id,
                   e.source, e.status, e.detail, e.created_at
            FROM ai.automation_events e
            JOIN ai.user_automations a ON a.id = e.automation_id
            WHERE e.status = 'failed'
            ORDER BY e.created_at DESC, e.id DESC
            LIMIT 20
            """
        )
        failures = [
            {
                "event_id": r[0], "automation_id": r[1], "automation_name": r[2],
                "tenant_id": r[3], "user_id": r[4], "source": r[5],
                "status": r[6], "detail": r[7],
                "created_at": r[8].isoformat() if r[8] else None,
            }
            for r in cur.fetchall()
        ]
    return {
        "total": total,
        "enabled": enabled,
        "by_trigger": {"cron": cron_n, "webhook": webhook_n},
        "recent_failures": failures,
    }


def admin_list_automations(*, limit: int = 100) -> list[dict]:
    """管理端跨租户清单（总览区块列表；不含任何密钥字段）。"""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT {_AUTOMATION_COLS} FROM ai.user_automations "
            "ORDER BY updated_at DESC LIMIT %s",
            (min(int(limit), 500),),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]
