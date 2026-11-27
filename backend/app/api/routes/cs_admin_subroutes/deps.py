"""CS Admin 认证鉴权、租户范围、会话访问控制与通用辅助。"""
from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Query, Request
from backend.app.api.routes.cs_admin_subroutes.base import (
    _CS_OPERATOR_ROLES,
    facade,
    logger,
)

def _use_java_source() -> bool:
    from backend.config.messaging import CS_ADMIN_SOURCE
    return CS_ADMIN_SOURCE == "java"


def _is_service_channel(request: Request) -> bool:
    from backend.config.auth import AUTH_TYPE_HEADER
    return (request.headers.get(AUTH_TYPE_HEADER) or "").strip().lower() == "api-key"


def _is_cs_operator(request: Request) -> bool:
    """判断请求是否来自客服工作台操作者。

    2026-09-21 客服端身份接线后开放两档：
    - 平台 admin（auth.users.role，历史语义：管理端时期的运营兜底）；
    - 客服域角色 agent/supervisor —— login/refresh 时由 _jwt_roles 并入
      JWT roles claim，经网关注入 X-User-Roles，此处只认头、不信客户端。
      坐席账号（平台角色 viewer/editor）由此可以进入坐席工作台。

    服务间 API-Key 由网关和 BFF 共同保护。普通客户 JWT 即使已登录
    （roles 无客服域角色），也不能读取他人的客服会话；写操作另经
    _resolve_agent_identity 强校验 cs_agents 绑定。
    """
    if facade._is_service_channel(request):
        return True

    from backend.app.api.identity import resolve_identity

    ident = resolve_identity(request)
    return ident.authenticated and bool(_CS_OPERATOR_ROLES & set(ident.roles))


def _require_cs_operator(request: Request) -> None:
    """客服工作台端点统一权限闸。"""
    if not facade._is_cs_operator(request):
        raise HTTPException(
            403, detail="客服坐席操作需要 admin 角色（或服务间 API Key）",
        )


def _resolve_tenant_scope(request: Request) -> str | None:
    """解析列表/统计类端点的租户过滤范围（STOP CS-A P0-2）。

    - 服务间 API-Key 通道（受信任的 BFF/脚本凭据）：返回 ``None`` = 跨租户
      服务视图（与派单 dispatcher 的跨租户语义一致）；
    - JWT 通道：返回网关验签后的 ``tenant_id``；缺失即可信身份不完整，
      fail-closed 403，绝不静默回落 'default'。
    """
    if facade._is_service_channel(request):
        return None

    from backend.app.api.identity import resolve_identity

    ident = resolve_identity(request)
    if not ident.tenant_id:
        raise HTTPException(403, detail="缺少可信租户身份")
    return ident.tenant_id


def _require_operator_and_tenant(request: Request) -> str | None:
    """operator 闸 + 租户范围解析的组合（list/stats/queue 共用）。"""
    facade._require_cs_operator(request)
    return facade._resolve_tenant_scope(request)


async def _resolve_agent_identity(request: Request, fallback_agent_id: str) -> str:
    agent_id, _tenant_id = await facade._resolve_agent_identity_and_tenant(
        request, fallback_agent_id
    )
    return agent_id


async def _resolve_agent_identity_and_tenant(
    request: Request, fallback_agent_id: str
) -> tuple[str, str | None]:
    """坐席身份解析 + 租户作用域（2026-11-08 跨租户审计）。

    返回 ``(agent_id, tenant_id)``：

    - JWT 通道：tenant_id 来自网关注入的可信身份（cs-claim-close 审计修复）；
      认领/关闭/发言据此加 ``tenant_id`` 谓词，他租坐席看到的是 404/409。
    - 服务间 API-Key 通道：tenant_id 为 ``None``（受信任 BFF 的跨租户服务
      视图，与 ``_resolve_tenant_scope`` 同语义），保持既有调用方行为。

    其余语义与旧 ``_resolve_agent_identity`` 一致：JWT 必须能按
    ``cs_agents.auth_user_id`` + 可信租户反查到启用坐席，客户端提交的
    agent_id 一律忽略。
    """
    from backend.app.api.identity import resolve_identity

    if facade._is_service_channel(request):
        agent_id = (fallback_agent_id or "").strip()
        if not agent_id:
            raise HTTPException(422, detail="agent_id is required")
        return agent_id, None

    ident = resolve_identity(request)
    if not ident.authenticated:
        raise HTTPException(401, detail="未认证：缺少坐席身份")
    if not ident.tenant_id:
        raise HTTPException(403, detail="缺少可信租户身份")

    try:
        agent_id = await facade._lookup_bound_agent_id(
            tenant_id=ident.tenant_id, user_id=ident.user_id
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("[CSAdmin] resolve agent identity failed: %s", exc)
        raise HTTPException(503, detail="Database unavailable") from exc

    if not agent_id:
        raise HTTPException(403, detail="当前用户未绑定启用的客服坐席")
    return str(agent_id), ident.tenant_id


async def _lookup_bound_agent_id(*, tenant_id: str, user_id: str) -> str | None:
    """按登录用户反查启用坐席 ID（坐席身份的唯一权威来源）。"""
    from backend.customer_service.dispatch import repository as dispatch_repo
    from backend.memory.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        return await dispatch_repo.find_enabled_agent_id(
            db, tenant_id=tenant_id, auth_user_id=user_id
        )


async def _ensure_conversation_access(
    request: Request,
    conv_user_id: str,
    conv_tenant_id: str | None = None,
    assigned_agent_id: str | None = None,
) -> None:
    """会话归属校验：登录用户只能读自己租户下自己的会话；guest 只能读匿名会话。

    普通用户必须同时满足 ``conv.user_id == ident.user_id`` 且
    ``conv.tenant_id == ident.tenant_id``（P0 多租户越权修复：仅校验
    user_id 会允许跨租户用户凭相同 user_id 串会话）。

    服务间 API-Key 通道（BFF 管理端）放行并记 warning —— 该通道由
    服务端凭据保护，用户归属在 BFF 信任边界内校验。

    缺陷7（2026-09-23）：认领/被派单坐席读会话消息与事件补发曾被 403
    （owner/admin 之外无通道）→ 工作台「选中会话暂无消息 / REST 对账与
    轮询降级全挂」。放行语义与 claim/close 的 ``_assert_current_agent``
    一致：只有 ``conv.assigned_agent_id`` 的**当前**认领坐席放行，不是
    「所有 cs_agent 读任意会话」，IDOR 隔离不变。坐席反查失败不在此
    拒绝 —— 请求方可能是会话主人（非坐席），继续走 owner 判定。
    """
    from backend.app.api.identity import resolve_identity

    if facade._is_service_channel(request):
        logger.warning(
            "[CSAdmin] api-key 通道访问会话（归属由 BFF 校验）: owner=%s",
            conv_user_id,
        )
        return

    ident = resolve_identity(request)
    if not ident.authenticated:
        # guest（未登录）：仅允许匿名演示会话
        if (conv_user_id or "").strip() in ("", "anonymous", "guest"):
            return
        raise HTTPException(403, detail="请登录后查看您的会话")

    # 租户隔离校验（跨租户严禁访问）
    if (
        conv_tenant_id is not None
        and ident.tenant_id
        and conv_tenant_id != ident.tenant_id
    ):
        raise HTTPException(403, detail="无权访问其他租户的会话")

    # 客服主管与管理员：在同租户内放行（支持监控/质检/重派）
    if {"admin", "super_admin", "supervisor"} & set(ident.roles):
        return

    # 客服坐席：已绑定并启用的坐席在同租户内放行（D13：支持会话管理列表详情展开与回放）
    if "agent" in ident.roles and ident.tenant_id:
        try:
            agent_id = await facade._lookup_bound_agent_id(
                tenant_id=ident.tenant_id, user_id=ident.user_id
            )
        except Exception as exc:
            logger.warning("[CSAdmin] resolve agent identity for read failed: %s", exc)
            agent_id = None
        if agent_id:
            return

    # 普通客户：严格本人校验（防止 IDOR 水平越权）
    if conv_user_id != ident.user_id:
        raise HTTPException(403, detail="无权访问他人会话")
    return


def _assert_current_agent(
    assigned_agent_id: str | None,
    agent_id: str,
) -> None:
    """限制人工操作只能由当前认领坐席执行。"""
    if not assigned_agent_id:
        raise HTTPException(409, detail="会话尚未绑定坐席，请重新认领")
    if assigned_agent_id != agent_id:
        raise HTTPException(409, detail="会话已由其他坐席认领")


async def _load_conversation_access_row(conversation_id: str):
    """读取会话归属三元组（user_id / tenant_id / assigned_agent_id），无行返回 None。"""
    from sqlalchemy import select

    from backend.customer_service.models.conversation import CSConversation
    from backend.memory.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        return (
            await db.execute(
                select(
                    CSConversation.user_id,
                    CSConversation.tenant_id,
                    CSConversation.assigned_agent_id,
                )
                .where(CSConversation.conversation_id == conversation_id)
                .limit(1)
            )
        ).first()


async def _ensure_pending_proposal_access(
    request: Request,
    conv_tenant_id: str | None,
    assigned_agent_id: str | None,
) -> None:
    """坐席侧待确认提案快照的可见性闸（cs-workstation-v2 P0）。

    任务契约只开放两档：supervisor/admin 看本租户全部会话；所属坐席
    （当前认领人）看自己的会话。api-key 服务通道放行（BFF 信任边界，
    与 _ensure_conversation_access 同语义）。普通用户的待确认快照走
    /my/{id}/pending，不得经由本端点读取他人会话。
    """
    if facade._is_service_channel(request):
        return

    from backend.app.api.identity import resolve_identity

    ident = resolve_identity(request)
    if not ident.authenticated:
        raise HTTPException(401, detail="未认证：请登录后访问坐席工作台")
    if not ident.tenant_id:
        raise HTTPException(403, detail="缺少可信租户身份")
    if conv_tenant_id and ident.tenant_id != conv_tenant_id:
        raise HTTPException(403, detail="无权访问其他租户的会话")
    if {"admin", "supervisor"} & set(ident.roles):
        return
    if "agent" in ident.roles and assigned_agent_id:
        try:
            agent_id = await facade._lookup_bound_agent_id(
                tenant_id=ident.tenant_id, user_id=ident.user_id
            )
        except Exception as exc:
            # 反查失败按「非当前坐席」处理，不让 DB 抖动放大成 500
            logger.warning(
                "[CSAdmin] resolve agent identity for pending snapshot failed: %s", exc,
            )
            agent_id = None
        if agent_id and str(agent_id) == str(assigned_agent_id):
            return
    raise HTTPException(403, detail="仅主管或会话所属坐席可查看待确认提案")


async def _proxy_to_java(path: str) -> dict:
    """代理请求到 business-service（cutover 后的读源）"""
    from backend.infra.http.business_client import BusinessServiceError, get_json

    try:
        return await get_json(path)
    except BusinessServiceError as e:
        if e.status_code == 404:
            raise HTTPException(404, detail="Conversation not found")
        # business-service 响应体可能含内部拓扑/路径，不透传给客户端
        logger.error(f"[cs_admin] business-service 调用失败: {e}")
        raise HTTPException(502, detail="下游业务服务暂不可用，请稍后重试") from e


async def _resolve_ws_agent_identity(request: Request) -> tuple[str, str]:
    """从可信 user/tenant 身份反查启用坐席，不接受客户端 agent_id。"""
    from backend.app.api.identity import resolve_identity
    from backend.config.auth import AUTH_TYPE_HEADER

    auth_type = (request.headers.get(AUTH_TYPE_HEADER) or "").strip().lower()
    if auth_type == "api-key":
        raise HTTPException(
            403,
            detail="WS ticket 需要绑定客服用户的 JWT 身份，API-Key 通道不支持",
        )

    identity = resolve_identity(request)
    if not identity.authenticated:
        raise HTTPException(401, detail="未认证：缺少坐席身份")
    if not identity.tenant_id:
        raise HTTPException(403, detail="缺少可信租户身份")

    try:
        agent_id = await facade._lookup_bound_agent_id(
            tenant_id=identity.tenant_id, user_id=identity.user_id
        )
    except Exception as exc:
        logger.warning("[CSAdmin] resolve ws agent failed: %s", exc)
        raise HTTPException(503, detail="Database unavailable") from exc

    if not agent_id:
        raise HTTPException(403, detail="当前用户未绑定启用的客服坐席")
    return str(agent_id), identity.tenant_id


async def _ensure_my_conversation(request: Request, conversation_id: str) -> None:
    """用户侧归属校验（/my/messages 与 /my/typing 共用）。

    登录强制（401 拒 guest）→ 本人会话精确匹配（user_id + tenant_id 双因子，
    与 _ensure_conversation_access 同口径，STOP CS-A P0-2）→ 404 不泄露
    存在性 → DB 故障 503。
    """
    from backend.app.api.identity import resolve_identity

    ident = resolve_identity(request)
    if not ident.authenticated:
        raise HTTPException(401, detail="未认证：请登录后访问会话")

    try:
        from sqlalchemy import select

        from backend.customer_service.models.conversation import CSConversation
        from backend.memory.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            row = (
                await db.execute(
                    select(
                        CSConversation.user_id,
                        CSConversation.tenant_id,
                    )
                    .where(CSConversation.conversation_id == conversation_id)
                    .limit(1)
                )
            ).first()
        if row is None:
            raise HTTPException(404, detail="Conversation not found")
        if row.user_id != ident.user_id:
            raise HTTPException(403, detail="无权访问他人会话")
        if (
            ident.tenant_id
            and row.tenant_id
            and row.tenant_id != ident.tenant_id
        ):
            raise HTTPException(403, detail="无权访问其他租户的会话")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[CSAdmin] my-conversation ownership check failed: {e}")
        raise HTTPException(503, detail="Database unavailable")


async def _ensure_cs_agent(db, agent_id: str) -> None:
    """确保认领所用的坐席档案存在，满足 assignment 的外键约束。

    JWT 坐席身份来自服务端解析；服务 API-Key 通道的 agent_id 来自受信任
    的 BFF。首次使用时补齐最小坐席档案，避免认领成功但 assignment 无法落库。
    """
    from sqlalchemy import select

    from backend.customer_service.models.agent import CSAgent

    existing = (
        await db.execute(
            select(CSAgent).where(CSAgent.agent_id == agent_id).limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return

    db.add(
        CSAgent(
            agent_id=agent_id,
            display_name=agent_id,
            role="agent",
            available=True,
        )
    )
    await db.flush()


async def _load_assigned_agent_id(
    db, conversation_id: str, tenant_id: str | None = None,
) -> str | None:
    """读取当前会话坐席归属，供幂等认领与并发冲突校验使用。

    ``tenant_id`` 非空时加租户谓词（JWT 坐席通道）；API-Key 服务通道传
    ``None`` 保持跨租户服务视图。
    """
    from sqlalchemy import select

    from backend.customer_service.models.conversation import CSConversation

    stmt = (
        select(CSConversation.assigned_agent_id)
        .where(CSConversation.conversation_id == conversation_id)
        .limit(1)
    )
    if tenant_id is not None:
        stmt = stmt.where(CSConversation.tenant_id == tenant_id)
    return (await db.execute(stmt)).scalar_one_or_none()

# 注册默认实现到 facade
for _fn in [
    _use_java_source,
    _is_service_channel,
    _is_cs_operator,
    _require_cs_operator,
    _resolve_tenant_scope,
    _require_operator_and_tenant,
    _resolve_agent_identity,
    _resolve_agent_identity_and_tenant,
    _lookup_bound_agent_id,
    _ensure_conversation_access,
    _assert_current_agent,
    _load_conversation_access_row,
    _ensure_pending_proposal_access,
    _proxy_to_java,
    _resolve_ws_agent_identity,
    _ensure_my_conversation,
    _ensure_cs_agent,
    _load_assigned_agent_id
]:
    facade.register(_fn.__name__, _fn)
