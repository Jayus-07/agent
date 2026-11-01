"""cs_inventory.py — 客服域意图目录（capability-governance G4 试点）

``GET /cs/capabilities/inventory?range=today|d7|d30``
  意图 × 调用/转人工/未解决 的运营只读视图，形状按 G5 域清单协议预期：
  ``{domain, range, total, intents:[{intent, description, stats:{calls,
  handoff_rate, unresolved}}], totals:{calls, intents_with_data, unresolved}}``

口径（与 P7 看板同源纪律，SQL 全静态 + 绑定参数）：
  - intent 清单与描述 = customer_service/router/intents.py::FINE_INTENTS
    （代码唯一事实源；无数据的意图照列，stats 为 0——目录是全集而非 Top）
  - calls          窗口内该意图 user 消息数（messages ⋈ conversations 收租户）
  - handoff_rate   该意图消息所属会话中发生人工接管（handoffs.assigned_agent_id
                   非空）的会话占比
  - unresolved     该意图**主导会话**出现在 ai.unanswered_questions（同租户同
                   窗口）的数量。会话→意图归属取窗口内 user 消息最多的意图
                   （row_number 稳定并列裁决）；cs conversation_id 与主图
                   session_id 同源（realtime.py 以前端传入的会话标识落库），
                   故以 unanswered.session_id = conversations.conversation_id
                   关联。这是运营近似口径：一次会话只有一个意图被记未解决。

权限（验收明确）：supervisor-only——意图目录是跨坐席运营视图，agent 仅本人
的口径对目录无意义（P7 口径矩阵之外的新视图，不沿用 agent=self）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.cs_agent_offers import (
    AgentContext,
    get_session,
    require_agent,
)
from backend.customer_service.router.intents import FINE_INTENTS

router = APIRouter(tags=["智能客服-能力目录"])


async def _require_inventory_viewer(request: Request) -> AgentContext:
    """目录身份闸：绑定坐席正常解析；未绑定的平台 admin 放行（重派端点
    同款 allow_unbound_admin 语义，身份仅用于审计与租户收口）。supervisor
    判定由下方 is_supervisor 承接（cs supervisor 或平台管理员）。"""
    return await require_agent(request, allow_unbound_admin=True)

_RANGE_DAYS = {"today": 1, "d7": 7, "d30": 30}

# 窗口内意图调用与人工接管会话数（supervisor=全租户）。
_INTENT_STATS_SQL = """
    SELECT m.intent_name                            AS intent_name,
           count(*)                                 AS calls,
           count(DISTINCT m.conversation_id)        AS conversations,
           count(DISTINCT h.conversation_id)        AS handoff_conversations
    FROM customer_service.messages m
    JOIN customer_service.conversations c
      ON c.conversation_id = m.conversation_id
    LEFT JOIN customer_service.handoffs h
      ON h.conversation_id = m.conversation_id
     AND h.tenant_id = c.tenant_id
     AND h.assigned_agent_id IS NOT NULL
    WHERE c.tenant_id = :tenant
      AND m.created_at >= :start AND m.created_at < :end
      AND m.role = 'user'
      AND m.intent_name IS NOT NULL AND m.intent_name <> ''
    GROUP BY m.intent_name
"""

# 未解决问题归属：每会话取窗口内 user 消息最多的意图（并列按名称稳定裁决），
# 只与 unanswered 命中的会话 join（静态 SQL，无字符串拼接）。
_UNRESOLVED_SQL = """
    WITH unresolved_sessions AS (
        SELECT session_id
        FROM ai.unanswered_questions
        WHERE tenant_id = :tenant
          AND created_at >= :start AND created_at < :end
          AND session_id <> ''
    ),
    intent_by_conversation AS (
        SELECT m.conversation_id,
               m.intent_name,
               row_number() OVER (
                   PARTITION BY m.conversation_id
                   ORDER BY count(*) DESC, m.intent_name
               ) AS rn
        FROM customer_service.messages m
        JOIN customer_service.conversations c
          ON c.conversation_id = m.conversation_id
        WHERE c.tenant_id = :tenant
          AND m.created_at >= :start AND m.created_at < :end
          AND m.role = 'user'
          AND m.intent_name IS NOT NULL AND m.intent_name <> ''
        GROUP BY m.conversation_id, m.intent_name
    )
    SELECT i.intent_name AS intent_name, count(*) AS unresolved
    FROM unresolved_sessions u
    JOIN intent_by_conversation i
      ON i.conversation_id = u.session_id AND i.rn = 1
    GROUP BY i.intent_name
"""


@router.get("/cs/capabilities/inventory")
async def capabilities_inventory(
    range_key: str = Query("d7", alias="range",
                           pattern="^(today|d7|d30)$"),
    context: AgentContext = Depends(_require_inventory_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """客服域意图目录（supervisor-only，运营只读）。"""
    if not context.is_supervisor:
        raise HTTPException(403, detail="意图目录为跨坐席运营视图，需要主管权限")

    from datetime import datetime, timedelta, timezone

    days = _RANGE_DAYS[range_key]
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    params = {"tenant": context.tenant_id, "start": start, "end": end}

    stats_result = await session.execute(text(_INTENT_STATS_SQL), params)
    unresolved_result = await session.execute(text(_UNRESOLVED_SQL), params)

    unresolved_by_intent = {
        r["intent_name"]: int(r["unresolved"]) for r in unresolved_result.mappings()
    }
    stats_by_intent = {
        r["intent_name"]: r for r in stats_result.mappings()
    }

    intents: list[dict[str, Any]] = []
    for name, description in FINE_INTENTS.items():
        row = stats_by_intent.get(name)
        conversations = int(row["conversations"]) if row else 0
        handoff_conversations = (
            int(row["handoff_conversations"]) if row else 0
        )
        intents.append({
            "intent": name,
            "description": description,
            "stats": {
                "calls": int(row["calls"]) if row else 0,
                "handoff_rate": (
                    round(handoff_conversations / conversations, 4)
                    if conversations else None
                ),
                "unresolved": unresolved_by_intent.get(name, 0),
            },
        })

    return {
        "domain": "customer_service",
        "range": range_key,
        "total": len(intents),
        "intents": intents,
        "totals": {
            "calls": sum(i["stats"]["calls"] for i in intents),
            "intents_with_data": sum(1 for i in intents if i["stats"]["calls"]),
            "unresolved": sum(unresolved_by_intent.values()),
        },
    }


__all__ = ["router"]
