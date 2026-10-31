"""customer_service/qa_sampling.py — 质检中心每日抽样 + AI 预填（P6）。

职责：
  1. compute_ai_prefill：零 LLM 启发式预填建议（首响延迟/满意度评分/转接
     尝试/SLA 超时四维扣分），落 ai_prefill JSONB——建议仅供参考，
     提交评分永远走人工（source=human）。
  2. sample_day：按批次日期抽取该日已关闭且指定了坐席的会话，为每租户
     取最近 N 条；UNIQUE(tenant_id, conversation_id) + ON CONFLICT DO
     NOTHING 保证同一会话至多抽检一次（重跑/多实例幂等）。

模式：与 qa_report.py 同款 —— 核心逻辑在本模块（可测试），Celery 薄壳
（tasks/cs_qa_tasks.py）只做注册包装。DB 不可用时抽样失败显式暴露。
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.shared.logger import logger

# 每租户每日抽样量默认值（可用 CS_QA_SAMPLE_SIZE 覆盖）。
DEFAULT_SAMPLE_SIZE = 10

_DIM_MAX = 25
_PASS_THRESHOLD = 60


def _day_range(batch_date: date) -> tuple[datetime, datetime]:
    """UTC 一天的 [start, end) 区间（按 conversations.closed_at 取数）。"""
    start = datetime.combine(batch_date, time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _clamp(value: int) -> int:
    return max(0, min(_DIM_MAX, value))


def compute_ai_prefill(facts: dict[str, Any]) -> dict[str, Any]:
    """按会话事实生成四维预填建议（确定性规则，零 LLM 依赖）。

    扣分口径（仅供人工参考，人工可任意调整）——权重偏严：预填的作用是
    把问题会话标出来（严重信号可给出 fail 建议），最终以人工终审为准：
      服务规范：用户满意度 rating<=2 −15 / 3 −4 / 4 −2
      准确性：  handoff attempt_count>1（转接/重派）每次 −4（封顶 −8）
      合规：    handoff sla_level=breach −12
      处理时效：首响 >60s −5；>300s −12
    """
    service = _DIM_MAX
    accuracy = _DIM_MAX
    compliance = _DIM_MAX
    timeliness = _DIM_MAX
    deductions: list[str] = []

    rating = facts.get("rating")
    if rating is not None:
        rating = int(rating)
        if rating <= 2:
            service -= 15
            deductions.append(f"用户评分 {rating} 星，明确不满（服务规范 −15）")
        elif rating == 3:
            service -= 4
            deductions.append(f"用户评分 {rating} 星（服务规范 −4）")
        elif rating == 4:
            service -= 2
            deductions.append(f"用户评分 {rating} 星（服务规范 −2）")

    attempts = int(facts.get("attempt_count") or 0)
    if attempts > 1:
        dock = min(8, 4 * (attempts - 1))
        accuracy -= dock
        deductions.append(f"会话经历 {attempts} 次派单尝试（准确性 −{dock}）")

    if facts.get("sla_breach"):
        compliance -= 12
        deductions.append("SLA 超时关闭（合规 −12）")

    first_reply = facts.get("first_reply_seconds")
    if first_reply is not None:
        first_reply = float(first_reply)
        if first_reply > 300:
            timeliness -= 12
            deductions.append(
                f"首次响应 {int(first_reply)}s 严重超时（处理时效 −12）"
            )
        elif first_reply > 60:
            timeliness -= 5
            deductions.append(
                f"首次响应 {int(first_reply)}s 偏慢（处理时效 −5）"
            )

    scores = {
        "service": _clamp(service),
        "accuracy": _clamp(accuracy),
        "compliance": _clamp(compliance),
        "timeliness": _clamp(timeliness),
    }
    total = sum(scores.values())
    verdict = "pass" if total >= _PASS_THRESHOLD else "fail"
    head = "（AI 预填建议 · source=ai，请人工终审）"
    note = head + ("主要扣分：" + "；".join(deductions) if deductions else "未发现明显扣分项")
    return {"scores": scores, "total": total, "verdict": verdict, "note": note}


_CANDIDATES_SQL = text("""
    SELECT h.tenant_id,
           h.conversation_id,
           h.assigned_agent_id,
           c.rating,
           EXTRACT(EPOCH FROM (c.first_reply_at - c.created_at))
               AS first_reply_seconds,
           (SELECT h2.attempt_count
              FROM customer_service.handoffs h2
             WHERE h2.conversation_id = h.conversation_id
             ORDER BY h2.updated_at DESC
             LIMIT 1)                                   AS attempt_count,
           EXISTS (SELECT 1 FROM customer_service.handoffs h3
                    WHERE h3.conversation_id = h.conversation_id
                      AND h3.sla_level = 'breach')      AS sla_breach
    FROM customer_service.handoffs h
    LEFT JOIN customer_service.conversations c
           ON c.conversation_id = h.conversation_id
          AND c.tenant_id = h.tenant_id
    WHERE h.tenant_id = :tenant
      AND h.handoff_state = 'closed'
      AND h.closed_at >= :start AND h.closed_at < :end
      AND h.assigned_agent_id IS NOT NULL
      AND NOT EXISTS (
            SELECT 1 FROM customer_service.cs_qa_scores q
             WHERE q.tenant_id = h.tenant_id
               AND q.conversation_id = h.conversation_id)
    ORDER BY h.closed_at DESC
    LIMIT :limit
""")


async def sample_for_tenant(
    session: AsyncSession,
    *,
    tenant_id: str,
    batch_date: date,
    limit: int = DEFAULT_SAMPLE_SIZE,
) -> int:
    """为单租户抽检一批已关闭人工会话，返回新建任务数（幂等）。

    口径（与 cs_admin 关闭路径一致）：conversations 无 'closed' 状态且关闭
    时 assigned_agent_id 被清空——关闭事实在 handoffs（handoff_state='closed'
    + closed_at），被检坐席取 handoff.assigned_agent_id。
    """
    start, end = _day_range(batch_date)
    rows = (
        await session.execute(
            _CANDIDATES_SQL,
            {"tenant": tenant_id, "start": start, "end": end, "limit": limit},
        )
    ).mappings().all()

    inserted = 0
    for row in rows:
        prefill = compute_ai_prefill(
            {
                "rating": row["rating"],
                "first_reply_seconds": row["first_reply_seconds"],
                "attempt_count": row["attempt_count"],
                "sla_breach": bool(row["sla_breach"]),
            }
        )
        result = await session.execute(text("""
            INSERT INTO customer_service.cs_qa_scores
                (tenant_id, conversation_id, agent_id, batch_date,
                 source, ai_prefill, status)
            VALUES (:tenant, :conversation, :agent, :batch,
                    'ai', CAST(:prefill AS jsonb), 'pending')
            ON CONFLICT (tenant_id, conversation_id) DO NOTHING
        """), {
            "tenant": row["tenant_id"],
            "conversation": row["conversation_id"],
            "agent": row["assigned_agent_id"],
            "batch": batch_date,
            "prefill": json.dumps(prefill, ensure_ascii=False),
        })
        inserted += result.rowcount or 0
    return inserted


async def sample_day(
    batch_date: date | None = None,
    sample_size: int | None = None,
) -> dict[str, Any]:
    """跨租户抽样一天批次；返回 {"ok", "batch_date", "sampled", "by_tenant"}。

    抽样日期默认昨天（beat 每日 06:05 UTC，先于 06:10 日报，保证当日
    日报的 qa_sampling 段能聚合到本批次人工/AI 抽检数据）。
    """
    from backend.memory.database import AsyncSessionLocal

    batch_date = batch_date or (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).date()
    limit = int(sample_size or DEFAULT_SAMPLE_SIZE)

    by_tenant: dict[str, int] = {}
    try:
        async with AsyncSessionLocal() as db, db.begin():
            tenants = (
                await db.execute(text("""
                    SELECT DISTINCT tenant_id
                    FROM customer_service.handoffs
                    WHERE handoff_state = 'closed'
                      AND closed_at >= :start AND closed_at < :end
                """), {"start": _day_range(batch_date)[0],
                      "end": _day_range(batch_date)[1]})
            ).scalars().all()
            for tenant in tenants:
                count = await sample_for_tenant(
                    db, tenant_id=str(tenant), batch_date=batch_date,
                    limit=limit,
                )
                if count:
                    by_tenant[str(tenant)] = count
    except Exception as exc:
        logger.error(
            "[QASampling] 抽样失败 batch=%s: %s", batch_date, exc,
            exc_info=True,
        )
        return {
            "ok": False,
            "error": str(exc),
            "batch_date": batch_date.isoformat(),
        }

    total = sum(by_tenant.values())
    logger.info(
        "[QASampling] 抽样完成 batch=%s sampled=%s by_tenant=%s",
        batch_date, total, by_tenant,
    )
    return {
        "ok": True,
        "batch_date": batch_date.isoformat(),
        "sampled": total,
        "by_tenant": by_tenant,
    }


def run_daily_sampling(
    batch_date: str | None = None, sample_size: int | None = None,
) -> dict[str, Any]:
    """同步入口（Celery 薄壳 / 手动触发）；batch_date=YYYY-MM-DD。"""
    from backend.customer_service._db_loop import run_sync

    parsed = date.fromisoformat(batch_date) if batch_date else None
    return run_sync(sample_day(parsed, sample_size))
