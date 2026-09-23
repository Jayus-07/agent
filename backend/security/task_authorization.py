"""task_authorization.py — 异步任务执行时授权解析（STOP D P0 收口，2026-09-23）

后台任务「身份固定、授权动态」模型：
  - 身份归属固定：actor（user_id/tenant_id）以 tasks 表持久化记录为准，
    不接受 query / tool arguments / resume body 提供的身份；
  - 授权权限动态：执行（含 resume）时按 actor 重新查 auth.users 的当前
    role/status/dept/tenant —— 用户被禁用、撤角色、移出租户后，即使任务
    已排队数小时也立即失效（不信任创建时 JWT roles 快照）。

失败语义 fail-closed：用户不存在 / 非启用 / 租户不匹配 / 身份不可解析 /
查询异常，一律抛 TaskAuthorizationDenied，由调用方把任务置 FAILED——
绝不回退 policy=None（授权未启用）旧行为。

角色/权限/data_scope 的推导完全复用 security/authorization.py 单一来源
（build_tool_authorization_context，与图/Tool 通道同一语义），本模块只做
「执行时当前授权」的数据库解析，不新建第二套 RBAC。
"""
from __future__ import annotations

from backend.security.authorization import (
    AuthorizationContext,
    build_tool_authorization_context,
)


class TaskAuthorizationDenied(PermissionError):
    """任务执行时授权解析失败或主体已失权（fail-closed 终态）。"""


# 与 Principal._UNAUTHENTICATED_USER_IDS 同口径：占位身份不是已认证主体
_UNAUTHENTICATED = frozenset({"", "default", "anonymous"})

# auth.users.status（migration 008）：1=启用 0=禁用
_USER_STATUS_ENABLED = 1


def _fetch_auth_user(uid: int):
    """查询 auth.users 当前权威行（role/status/dept/tenant_id）。

    独立成模块级函数仅为测试可注入（DB 是外部边界，只 mock 这一处），
    生产语义 = 与登录/JWT 签发同一权威数据源。连接与 task_service 同库
    同源（MEMORY_DB_CONFIG，auth schema 所在库）。
    """
    import psycopg

    from backend.config.database import MEMORY_DB_CONFIG

    c = MEMORY_DB_CONFIG
    dsn = (f"postgresql://{c['user']}:{c['password']}"
           f"@{c['host']}:{c['port']}/{c['dbname']}")
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT role, status, dept, tenant_id FROM auth.users "
            "WHERE id = %s", (uid,))
        return cur.fetchone()


def resolve_task_authorization(user_id: str, tenant_id: str) -> AuthorizationContext:
    """任务持久化 actor → 当前权威 AuthorizationContext（无 HTTP 环境）。

    - user_id 必须是 auth.users.id 的十进制字符串（网关 X-User-Id 口径）；
      legacy body 自由串不可映射真实主体 → 拒绝（可伪造身份不进授权）。
    - tenant_id 必须与 auth.users.tenant_id 当前值一致（membership 撤销
      或换租户后旧任务拒绝执行）。
    - 授权推导（roles → permission_codes/data_scope）交
      build_tool_authorization_context，本函数不复制任何映射。
    """
    raw_uid = str(user_id or "").strip()
    tid = str(tenant_id or "").strip()
    if raw_uid in _UNAUTHENTICATED or tid in _UNAUTHENTICATED:
        raise TaskAuthorizationDenied(
            f"任务 actor 身份不可信（user_id={raw_uid!r}, "
            f"tenant_id={tid!r}），拒绝执行")
    try:
        uid = int(raw_uid)
    except ValueError:
        raise TaskAuthorizationDenied(
            f"任务 user_id={raw_uid!r} 不是 auth.users.id 口径，拒绝执行")

    try:
        row = _fetch_auth_user(uid)
    except Exception as exc:
        raise TaskAuthorizationDenied(
            f"授权解析查询失败（fail-closed）: {exc}") from exc

    if row is None:
        raise TaskAuthorizationDenied(f"用户不存在: user_id={uid}")
    role, status, dept, user_tenant = row
    if int(status or 0) != _USER_STATUS_ENABLED:
        raise TaskAuthorizationDenied(
            f"用户已禁用: user_id={uid} (status={status})")
    if str(user_tenant or "").strip() != tid:
        raise TaskAuthorizationDenied(
            f"租户 membership 不匹配: user_id={uid} "
            f"task_tenant={tid!r} user_tenant={user_tenant!r}")

    return build_tool_authorization_context(
        user_id=str(uid),
        department=str(dept or ""),
        tenant_id=tid,
        roles=(str(role).strip(),) if role else (),
    )
