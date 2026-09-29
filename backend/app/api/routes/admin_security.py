"""安全事件查询 API（M9 / 台账 D9）

GET /admin/security/events — 安全事件分页查询（类型/用户/时间窗过滤）
GET /admin/security/stats  — 四类事件计数 + category 分布（Dashboard 消费）

数据源：ai.security_events（security/events.py 旁路写入）。权限：管理员。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from backend.app.api.deps import require_admin_user

router = APIRouter(prefix="/admin/security", tags=["Admin-Security"])


def _query(sql: str, params: tuple) -> list[tuple]:
    from backend.config.database import OBS_DB_PG_CONFIG
    from backend.infra.db import engine_for

    with engine_for(OBS_DB_PG_CONFIG).raw_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()


def _hours_cutoff(window_h: int) -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - window_h * 3600))


@router.get("/events")
async def list_security_events(
    request: Request,
    event_type: str = Query("", description="事件类型过滤（空=全部）"),
    user_id: str = Query("", description="用户过滤（精确）"),
    window_h: int = Query(24, ge=1, le=24 * 30, description="时间窗（小时）"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=200),
):
    await require_admin_user(request)
    cutoff = _hours_cutoff(window_h)
    where = ["created_at >= %s"]
    params: list[Any] = [cutoff]
    if event_type:
        where.append("event_type = %s")
        params.append(event_type)
    if user_id:
        where.append("user_id = %s")
        params.append(user_id)
    where_sql = " AND ".join(where)

    rows = _query(
        f"SELECT id, created_at, event_type, category, user_id, tenant_id, trace_id, "
        f"session_id, detail FROM ai.security_events WHERE {where_sql} "
        f"ORDER BY id DESC LIMIT %s OFFSET %s",
        (*params, page_size, (page - 1) * page_size),
    )
    total = _query(
        f"SELECT COUNT(*) FROM ai.security_events WHERE {where_sql}", tuple(params)
    )[0][0]
    import json

    events = [
        {
            "id": r[0], "ts": str(r[1]), "event_type": r[2], "category": r[3],
            "user_id": r[4], "tenant_id": r[5], "trace_id": r[6],
            "session_id": r[7], "detail": r[8] if isinstance(r[8], dict)
            else json.loads(r[8] or "{}"),
        }
        for r in rows
    ]
    return {"total": total, "page": page, "page_size": page_size, "events": events}


@router.get("/stats")
async def security_stats(
    request: Request,
    window_h: int = Query(24, ge=1, le=24 * 30),
):
    await require_admin_user(request)
    cutoff = _hours_cutoff(window_h)
    by_type = {
        r[0]: r[1] for r in _query(
            "SELECT event_type, COUNT(*) FROM ai.security_events "
            "WHERE created_at >= %s GROUP BY event_type", (cutoff,))}
    by_category = [
        {"event_type": r[0], "category": r[1], "count": r[2]}
        for r in _query(
            "SELECT event_type, category, COUNT(*) FROM ai.security_events "
            "WHERE created_at >= %s GROUP BY event_type, category "
            "ORDER BY COUNT(*) DESC LIMIT 30", (cutoff,))
    ]
    top_users = [
        {"user_id": r[0], "count": r[1]}
        for r in _query(
            "SELECT user_id, COUNT(*) FROM ai.security_events "
            "WHERE created_at >= %s AND user_id <> '' "
            "GROUP BY user_id ORDER BY COUNT(*) DESC LIMIT 10", (cutoff,))
    ]
    return {"window_h": window_h, "by_type": by_type,
            "by_category": by_category, "top_users": top_users}


__all__ = ["router"]
