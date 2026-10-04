"""未答问题查询 API（2026-10-03 知识运营闭环）

GET /admin/unanswered/questions — 拒答未答问题分页查询（来源/时间窗过滤）
GET /admin/unanswered/stats     — 来源分布 + 高频未答问题（按归一化哈希聚类）

数据源：ai.unanswered_questions（observability/unanswered.py 旁路写入）。
权限：管理员。与 M9 security 查询端点同一查询范式。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from backend.app.api.deps import require_admin_user

router = APIRouter(prefix="/admin/unanswered", tags=["Admin-Unanswered"])


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


@router.get("/questions")
async def list_unanswered_questions(
    request: Request,
    source: str = Query("", description="来源过滤（rag_miss|sql_empty，空=全部）"),
    window_h: int = Query(24 * 7, ge=1, le=24 * 90, description="时间窗（小时）"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=10, le=200),
):
    await require_admin_user(request)
    cutoff = _hours_cutoff(window_h)
    where = ["created_at >= %s"]
    params: list[Any] = [cutoff]
    if source:
        where.append("source = %s")
        params.append(source)
    where_sql = " AND ".join(where)

    rows = _query(
        f"SELECT id, created_at, source, question, question_hash, user_id, "
        f"tenant_id, department, kb_id, session_id, detail "
        f"FROM ai.unanswered_questions WHERE {where_sql} "
        f"ORDER BY id DESC LIMIT %s OFFSET %s",
        (*params, page_size, (page - 1) * page_size),
    )
    total = _query(
        f"SELECT COUNT(*) FROM ai.unanswered_questions WHERE {where_sql}",
        tuple(params),
    )[0][0]
    import json

    items = [
        {
            "id": r[0], "ts": str(r[1]), "source": r[2], "question": r[3],
            "question_hash": r[4], "user_id": r[5], "tenant_id": r[6],
            "department": r[7], "kb_id": r[8], "session_id": r[9],
            "detail": r[10] if isinstance(r[10], dict)
            else json.loads(r[10] or "{}"),
        }
        for r in rows
    ]
    return {"total": total, "page": page, "page_size": page_size, "questions": items}


@router.get("/stats")
async def unanswered_stats(
    request: Request,
    window_h: int = Query(24 * 7, ge=1, le=24 * 90),
):
    await require_admin_user(request)
    cutoff = _hours_cutoff(window_h)
    by_source = {
        r[0]: r[1] for r in _query(
            "SELECT source, COUNT(*) FROM ai.unanswered_questions "
            "WHERE created_at >= %s GROUP BY source", (cutoff,))
    }
    # 高频未答问题：归一化哈希聚类取代表问法（同哈希取最新一条原文）
    top_questions = [
        {"question_hash": r[0], "count": r[1], "sample_question": r[2],
         "source": r[3]}
        for r in _query(
            """
            SELECT t.question_hash, t.cnt, t.question, t.source FROM (
                SELECT question_hash, COUNT(*) AS cnt,
                       (ARRAY_AGG(question ORDER BY id DESC))[1] AS question,
                       (ARRAY_AGG(source ORDER BY id DESC))[1] AS source
                FROM ai.unanswered_questions
                WHERE created_at >= %s
                GROUP BY question_hash
            ) t ORDER BY t.cnt DESC LIMIT 20
            """,
            (cutoff,))
    ]
    return {"window_h": window_h, "by_source": by_source,
            "top_questions": top_questions}


__all__ = ["router"]
