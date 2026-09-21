"""客服派单运营统计（P8）。

``GET /cs/ops/dispatch/stats`` —— DB 实时快照（排队/在派/启用坐席/outbox
积压与 lag）。Prometheus 时序走 ``/metrics``（cs_dispatch_* / cs_outbox_*），
本端点用于运营后台的一次性巡检与告警复核，二者口径一致。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.customer_service.dispatch import repository
from backend.memory.database import AsyncSessionLocal, MemoryDatabaseUnavailable

router = APIRouter(tags=["智能客服-派单运营"])


async def require_cs_supervisor(request: Request):
    """派单运营快照权限闸（2026-09-21 客服端身份接线）。

    允许两档：平台 admin（auth.users.role）与客服主管
    （cs_agents.role=supervisor，经 JWT roles claim → 网关 X-User-Roles
    注入，客户端不可伪造）。api-key 服务通道不映射用户身份，一律 403，
    与 deps.require_admin_user 的 kind 语义一致。
    """
    from backend.app.api.identity import resolve_identity

    ident = resolve_identity(request)
    if ident.authenticated and ({"admin", "supervisor"} & set(ident.roles)):
        return ident
    raise HTTPException(
        403, detail="派单运营快照仅限管理员或客服主管（supervisor）访问",
    )


@router.get("/cs/ops/dispatch/stats")
async def dispatch_stats(
    request: Request,
    _operator=Depends(require_cs_supervisor),
):
    """派单域运营快照（管理员）。

    - ``queue``：waiting_human / agent_offered 工单数（含按租户分布）
    - ``agents``：启用坐席数（在线与否由 Redis presence 决定，见 /metrics
      的 ``cs_dispatch_online_agents``，由 worker 每秒刷新）
    - ``outbox``：pending 事件数与最旧事件 lag（秒）
    """
    try:
        async with AsyncSessionLocal() as session:
            waiting = await repository.count_queue_by_tenant(session)
            offering = await repository.count_offering_by_tenant(session)
            enabled = await repository.count_enabled_agents_by_tenant(session)
            pending = await repository.count_pending_outbox(session)
            oldest = await repository.oldest_pending_outbox_created_at(session)
    except MemoryDatabaseUnavailable as exc:
        raise HTTPException(503, detail="Database unavailable") from exc

    now = datetime.now(timezone.utc)
    lag_seconds = (
        max(0.0, (now - oldest).total_seconds()) if oldest is not None else 0.0
    )
    return {
        "queue": {
            "waiting_total": sum(waiting.values()),
            "offered_total": sum(offering.values()),
            "waiting_by_tenant": waiting,
            "offered_by_tenant": offering,
        },
        "agents": {
            "enabled_total": sum(enabled.values()),
            "enabled_by_tenant": enabled,
        },
        "outbox": {
            "pending": pending,
            "oldest_pending_lag_seconds": round(lag_seconds, 3),
        },
        "generated_at": now.isoformat(),
    }
