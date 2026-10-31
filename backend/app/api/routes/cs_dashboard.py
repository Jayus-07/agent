"""cs_dashboard.py — 数据看板聚合（cs-workstation-v2 P7）

``GET /cs/dashboard?range=today|d7|d30``
  - cards：六卡（排队中/进行中=实时全局；接待量/转人工率/平均首响/CSAT=窗口
    聚合 + 对比前一等长窗口的涨跌）
  - trend：近 7 日接待量/转人工双序列（固定 7 日，对齐原型）
  - intents：窗口内用户消息意图 Top（join conversations 收租户/坐席归属）
  - agents：坐席绩效表（接待量/平均首响/AHT/CSAT/质检分/当前状态）

角色口径（矩阵 3.1）：supervisor=全租户；agent=仅本人（会话归属经 handoffs
判定——conversations.assigned_agent_id 关闭即清空，不可作归属事实源）。
意图分布 agent 视角过滤本人会话；排队/进行中为共享队列实时快照（两视角一致）。
SQL 全部为静态字符串 + 绑定参数（scope 差异用两条常量 SQL 表达，无字符串拼接）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.cs_agent_offers import (
    AgentContext,
    get_session,
    require_agent,
)

router = APIRouter(tags=["智能客服-数据看板"])

_RANGE_DAYS = {"today": 1, "d7": 7, "d30": 30}

# 窗口聚合（ALL=全租户 / SELF=仅本人 handoffs 归属）——两条静态 SQL。
_AGG_SQL = {
    "all": """
        SELECT count(*)                                              AS received,
               count(*) FILTER (WHERE c.first_reply_at IS NOT NULL
                                  AND c.first_reply_at >= c.created_at)
                                                                        AS with_reply,
               avg(EXTRACT(EPOCH FROM (c.first_reply_at - c.created_at)))
                   FILTER (WHERE c.first_reply_at IS NOT NULL
                            AND c.first_reply_at >= c.created_at)     AS avg_first_reply,
               avg(c.rating)                                          AS csat
        FROM customer_service.conversations c
        WHERE c.tenant_id = :tenant
          AND c.created_at >= :a AND c.created_at < :b
    """,
    "self": """
        SELECT count(*)                                              AS received,
               count(*) FILTER (WHERE c.first_reply_at IS NOT NULL
                                  AND c.first_reply_at >= c.created_at)
                                                                        AS with_reply,
               avg(EXTRACT(EPOCH FROM (c.first_reply_at - c.created_at)))
                   FILTER (WHERE c.first_reply_at IS NOT NULL
                            AND c.first_reply_at >= c.created_at)     AS avg_first_reply,
               avg(c.rating)                                          AS csat
        FROM customer_service.conversations c
        WHERE c.tenant_id = :tenant
          AND c.created_at >= :a AND c.created_at < :b
          AND EXISTS (SELECT 1 FROM customer_service.handoffs hh
                       WHERE hh.conversation_id = c.conversation_id
                         AND hh.tenant_id = c.tenant_id
                         AND hh.assigned_agent_id = :agent)
    """,
}

# 近 7 日趋势（转人工序列：ALL=全部坐席 / SELF=仅本人）。
_TREND_SQL = {
    "all": """
        SELECT to_char(d.day, 'MM-DD')                          AS label,
               count(DISTINCT c.conversation_id)                AS received,
               count(DISTINCT h.conversation_id)                AS human
        FROM generate_series(
               (current_date AT TIME ZONE 'UTC')::date - interval '6 days',
               (current_date AT TIME ZONE 'UTC')::date,
               interval '1 day') AS d(day)
        LEFT JOIN customer_service.conversations c
               ON c.tenant_id = :tenant
              AND c.created_at >= d.day
              AND c.created_at < d.day + interval '1 day'
        LEFT JOIN customer_service.handoffs h
               ON h.tenant_id = :tenant
              AND h.created_at >= d.day
              AND h.created_at < d.day + interval '1 day'
              AND h.assigned_agent_id IS NOT NULL
              AND (c.conversation_id IS NULL
                   OR h.conversation_id = c.conversation_id)
        GROUP BY d.day
        ORDER BY d.day
    """,
    "self": """
        SELECT to_char(d.day, 'MM-DD')                          AS label,
               count(DISTINCT c.conversation_id)                AS received,
               count(DISTINCT h.conversation_id)                AS human
        FROM generate_series(
               (current_date AT TIME ZONE 'UTC')::date - interval '6 days',
               (current_date AT TIME ZONE 'UTC')::date,
               interval '1 day') AS d(day)
        LEFT JOIN customer_service.conversations c
               ON c.tenant_id = :tenant
              AND c.created_at >= d.day
              AND c.created_at < d.day + interval '1 day'
        LEFT JOIN customer_service.handoffs h
               ON h.tenant_id = :tenant
              AND h.created_at >= d.day
              AND h.created_at < d.day + interval '1 day'
              AND h.assigned_agent_id = :agent
              AND (c.conversation_id IS NULL
                   OR h.conversation_id = c.conversation_id)
        GROUP BY d.day
        ORDER BY d.day
    """,
}

# 意图分布（join conversations 收租户；SELF 追加本人 handoffs 归属过滤）。
_INTENTS_SQL = {
    "all": """
        SELECT m.intent_name, count(*) AS cnt
        FROM customer_service.messages m
        JOIN customer_service.conversations c
          ON c.conversation_id = m.conversation_id
        WHERE c.tenant_id = :tenant
          AND m.created_at >= :start AND m.created_at < :end
          AND m.role = 'user'
          AND m.intent_name IS NOT NULL AND m.intent_name <> ''
        GROUP BY m.intent_name
        ORDER BY cnt DESC
        LIMIT 8
    """,
    "self": """
        SELECT m.intent_name, count(*) AS cnt
        FROM customer_service.messages m
        JOIN customer_service.conversations c
          ON c.conversation_id = m.conversation_id
        WHERE c.tenant_id = :tenant
          AND m.created_at >= :start AND m.created_at < :end
          AND m.role = 'user'
          AND m.intent_name IS NOT NULL AND m.intent_name <> ''
          AND EXISTS (SELECT 1 FROM customer_service.handoffs hh
                       WHERE hh.conversation_id = m.conversation_id
                         AND hh.tenant_id = c.tenant_id
                         AND hh.assigned_agent_id = :agent)
        GROUP BY m.intent_name
        ORDER BY cnt DESC
        LIMIT 8
    """,
}


def _window(range_key: str) -> tuple[datetime, datetime, datetime]:
    """返回 (start, end, prev_start)：窗口与前一等长窗口（UTC）。"""
    days = _RANGE_DAYS[range_key]
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return start, end, start - timedelta(days=days)


def _pct_delta(current: float | None, previous: float | None) -> float | None:
    """涨跌百分比（previous=0 时返回 None，不伪造）。"""
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / previous, 4)


async def _scalar(
    session: AsyncSession, sql: str, params: dict,
) -> Any:
    return (await session.execute(text(sql), params)).mappings().one()


async def _cards(
    session: AsyncSession, *, context: AgentContext,
    start: datetime, end: datetime, prev_start: datetime,
) -> dict:
    """六卡：两个实时 + 四个窗口聚合（含与前一窗口对比）。"""
    scope = "all" if context.is_supervisor else "self"
    base_params = {"tenant": context.tenant_id, "agent": context.agent_id}

    async def _agg(a: datetime, b: datetime) -> dict:
        row = await _scalar(
            session, _AGG_SQL[scope], {**base_params, "a": a, "b": b},
        )
        handoff_row = await _scalar(session, """
            SELECT count(*) AS human
            FROM customer_service.handoffs h
            WHERE h.tenant_id = :tenant
              AND h.created_at >= :a AND h.created_at < :b
        """, {"tenant": context.tenant_id, "a": a, "b": b})
        received = int(row["received"] or 0)
        human = int(handoff_row["human"] or 0)
        return {
            "received": received,
            "human": human,
            "handoff_rate": (human / received) if received else None,
            "avg_first_reply_s": (
                float(row["avg_first_reply"])
                if row["avg_first_reply"] is not None else None
            ),
            "csat": (
                round(float(row["csat"]), 2)
                if row["csat"] is not None else None
            ),
        }

    current = await _agg(start, end)
    previous = await _agg(prev_start, end)

    # 实时快照（共享队列，两视角一致）
    queue_row = await _scalar(session, """
        SELECT count(*) FILTER (WHERE handoff_state IN
                                    ('waiting_human', 'agent_offered')) AS queue,
               count(*) FILTER (WHERE handoff_state = 'human_active') AS active
        FROM customer_service.handoffs
        WHERE tenant_id = :tenant
    """, {"tenant": context.tenant_id})
    open_row = await _scalar(session, """
        SELECT count(*) AS open_convs
        FROM customer_service.conversations
        WHERE tenant_id = :tenant AND conversation_status = 'open'
    """, {"tenant": context.tenant_id})

    return {
        "queue_waiting": int(queue_row["queue"] or 0),
        "in_progress": int(open_row["open_convs"] or 0),
        "received": {
            "value": current["received"],
            "delta_pct": _pct_delta(current["received"], previous["received"]),
        },
        "handoff_rate": {
            "value": (
                round(current["handoff_rate"], 4)
                if current["handoff_rate"] is not None else None
            ),
            "delta_pp": (
                round(
                    (current["handoff_rate"] - previous["handoff_rate"]) * 100, 1
                )
                if current["handoff_rate"] is not None
                and previous["handoff_rate"] is not None else None
            ),
        },
        "avg_first_reply_s": {
            "value": (
                round(current["avg_first_reply_s"])
                if current["avg_first_reply_s"] is not None else None
            ),
            "delta_s": (
                round(
                    current["avg_first_reply_s"] - previous["avg_first_reply_s"]
                )
                if current["avg_first_reply_s"] is not None
                and previous["avg_first_reply_s"] is not None else None
            ),
        },
        "csat": {
            "value": current["csat"],
            "delta": (
                round(current["csat"] - previous["csat"], 2)
                if current["csat"] is not None and previous["csat"] is not None
                else None
            ),
        },
    }


async def _trend(session: AsyncSession, *, context: AgentContext) -> list[dict]:
    """近 7 日接待量/转人工双序列（scope=self 过滤本人 handoffs）。"""
    scope = "all" if context.is_supervisor else "self"
    rows = (
        await session.execute(
            text(_TREND_SQL[scope]),
            {"tenant": context.tenant_id, "agent": context.agent_id},
        )
    ).mappings().all()
    return [
        {
            "date": row["label"],
            "received": int(row["received"] or 0),
            "human": int(row["human"] or 0),
        }
        for row in rows
    ]


async def _intents(
    session: AsyncSession, *, context: AgentContext,
    start: datetime, end: datetime,
) -> list[dict]:
    """窗口内用户消息意图 Top8（join conversations 收租户；agent 过滤本人）。"""
    scope = "all" if context.is_supervisor else "self"
    rows = (
        await session.execute(
            text(_INTENTS_SQL[scope]),
            {
                "tenant": context.tenant_id, "agent": context.agent_id,
                "start": start, "end": end,
            },
        )
    ).mappings().all()
    total = sum(int(r["cnt"]) for r in rows) or 1
    return [
        {
            "intent": r["intent_name"],
            "count": int(r["cnt"]),
            "pct": round(int(r["cnt"]) / total, 4),
        }
        for r in rows
    ]


async def _agents(
    session: AsyncSession, *, context: AgentContext,
    start: datetime, end: datetime,
) -> list[dict]:
    """坐席绩效表（supervisor 全员 / agent 仅本人一行）。"""
    agent_filter = None if context.is_supervisor else context.agent_id
    rows = (
        await session.execute(text("""
            WITH h AS (
                SELECT assigned_agent_id, conversation_id,
                       EXTRACT(EPOCH FROM (closed_at - created_at)) AS handle_s
                FROM customer_service.handoffs
                WHERE tenant_id = :tenant
                  AND created_at >= :start AND created_at < :end
                  AND assigned_agent_id IS NOT NULL
            ),
            perf AS (
                SELECT assigned_agent_id,
                       count(DISTINCT conversation_id) AS handled,
                       avg(handle_s) AS aht_s
                FROM h GROUP BY assigned_agent_id
            ),
            fr AS (
                SELECT h.assigned_agent_id,
                       avg(EXTRACT(EPOCH FROM (c.first_reply_at - c.created_at)))
                           AS fr_s
                FROM h
                JOIN customer_service.conversations c
                  ON c.conversation_id = h.conversation_id
                 AND c.tenant_id = :tenant
                WHERE c.first_reply_at IS NOT NULL
                  AND c.first_reply_at >= c.created_at
                GROUP BY h.assigned_agent_id
            ),
            cs AS (
                SELECT h.assigned_agent_id, avg(c.rating) AS csat
                FROM h
                JOIN customer_service.conversations c
                  ON c.conversation_id = h.conversation_id
                 AND c.tenant_id = :tenant
                WHERE c.rating IS NOT NULL
                GROUP BY h.assigned_agent_id
            ),
            qa AS (
                SELECT agent_id,
                       round(avg(COALESCE(final_score, total_score))::numeric, 1)
                           AS qa_score
                FROM customer_service.cs_qa_scores
                WHERE tenant_id = :tenant
                  AND COALESCE(final_score, total_score) IS NOT NULL
                  AND batch_date >= (:start)::date
                GROUP BY agent_id
            )
            SELECT a.agent_id,
                   a.display_name,
                   a.status,
                   coalesce(perf.handled, 0)                       AS handled,
                   fr.fr_s                                         AS fr_s,
                   perf.aht_s                                      AS aht_s,
                   cs.csat                                         AS csat,
                   qa.qa_score                                     AS qa_score
            FROM customer_service.cs_agents a
            LEFT JOIN perf ON perf.assigned_agent_id = a.agent_id
            LEFT JOIN fr ON fr.assigned_agent_id = a.agent_id
            LEFT JOIN cs ON cs.assigned_agent_id = a.agent_id
            LEFT JOIN qa ON qa.agent_id = a.agent_id
            WHERE a.tenant_id = :tenant AND a.enabled = TRUE
              AND (CAST(:agent_filter AS text) IS NULL
                   OR a.agent_id = CAST(:agent_filter AS text))
            ORDER BY coalesce(perf.handled, 0) DESC, a.agent_id
        """), {
            "tenant": context.tenant_id, "start": start, "end": end,
            "agent_filter": agent_filter,
        })
    ).mappings().all()
    return [
        {
            "agent_id": r["agent_id"],
            "display_name": r["display_name"],
            "status": r["status"],
            "handled": int(r["handled"] or 0),
            "avg_first_reply_s": (
                round(float(r["fr_s"])) if r["fr_s"] is not None else None
            ),
            "aht_s": round(float(r["aht_s"])) if r["aht_s"] is not None else None,
            "csat": round(float(r["csat"]), 2) if r["csat"] is not None else None,
            "qa_score": float(r["qa_score"]) if r["qa_score"] is not None else None,
        }
        for r in rows
    ]


@router.get("/cs/dashboard")
async def dashboard(
    range: str = Query(default="today"),
    context: AgentContext = Depends(require_agent),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """看板聚合（supervisor 全租户 / agent 仅本人）。"""
    if range not in _RANGE_DAYS:
        raise HTTPException(422, detail="range 仅支持 today|d7|d30")
    start, end, prev_start = _window(range)
    return {
        "scope": "all" if context.is_supervisor else "self",
        "range": range,
        "cards": await _cards(
            session, context=context, start=start, end=end, prev_start=prev_start,
        ),
        "trend": await _trend(session, context=context),
        "intents": await _intents(
            session, context=context, start=start, end=end,
        ),
        "agents": await _agents(
            session, context=context, start=start, end=end,
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
