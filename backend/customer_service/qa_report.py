"""customer_service/qa_report.py — 客服质检每日报表（批次D）。

职责：聚合指定日期（默认昨天）的运营指标 → 写 qa_daily_reports
（tenant + report_date 幂等覆盖，beat 重跑安全）→ 发 Prometheus gauge。

指标口径（全部基于 customer_service schema 既有字段，无需新采集点）：
  conversations   会话量 / 关闭量 / 转人工量 / 转人工率 / AI 独立关闭率
  satisfaction    评价量 / 均分 / 1-5 星分布
  response        首次响应时长：60s 内达标量与达标率（conversation.first_reply_at）
  top_intents     用户消息意图 Top10（messages.role='user'，含空值排除）
  agents          坐席维度：承接会话数 / 其服务会话满意度均分
  tickets         新增量 / 按类型分布 / 超 48h 未关闭数（依赖批次C）

模式：与 maintenance.py 同款 —— 核心逻辑在本模块（可测试），Celery 薄壳
（tasks/cs_qa_tasks.py）只做注册包装。DB 不可用时报表失败显式暴露（ok=False），
绝不静默吞掉（日报缺失会让管理端误判运营正常）。
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from prometheus_client import Gauge
from sqlalchemy import text

from backend.shared.logger import logger

# FAQ 周聚合 gauge（G 告警数据源，2026-10-04）：随日报产出、随 _record_prometheus_safe
# 刷新，滚动 7 天口径。定义在本模块而非中心 metrics.py——这两个序列的唯一
# 生产者是质检日报，就地定义避免中心注册表为单一消费方扇出；multiproc 聚合
# 端点（worker_metrics :9809）读默认 registry，本模块注册同样被采集。
cs_qa_daily_faq_hit_ratio = Gauge(
    "cs_qa_daily_faq_hit_ratio",
    "客服质检日报：FAQ 近 7 天承接占比（命中/总查询）",
    multiprocess_mode="max",
)
cs_qa_daily_faq_published = Gauge(
    "cs_qa_daily_faq_published",
    "客服质检日报：FAQ published 条目数",
    multiprocess_mode="max",
)


def _day_range(report_date: date) -> tuple[datetime, datetime]:
    """UTC 一天的 [start, end) 区间。"""
    start = datetime.combine(report_date, time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


async def _collect_metrics(report_date: date, tenant_id: str) -> dict[str, Any]:
    from backend.memory.database import AsyncSessionLocal

    start, end = _day_range(report_date)
    metrics: dict[str, Any] = {"report_date": report_date.isoformat()}

    async with AsyncSessionLocal() as db:
        # ── 会话量与模式 ──
        row = (
            await db.execute(text("""
                SELECT count(*)                                   AS total,
                       count(*) FILTER (WHERE conversation_status = 'closed')
                                                                         AS closed,
                       count(*) FILTER (WHERE handling_mode = 'human')   AS human,
                       count(*) FILTER (WHERE handling_mode = 'ai'
                                          AND conversation_status = 'closed')
                                                                         AS ai_closed,
                       count(*) FILTER (WHERE first_reply_at IS NOT NULL
                                          AND first_reply_at <= created_at + interval '60 seconds')
                                                                         AS fast_first_reply,
                       count(*) FILTER (WHERE first_reply_at IS NOT NULL)
                                                                         AS has_first_reply
                FROM customer_service.conversations
                WHERE tenant_id = :tenant
                  AND created_at >= :start AND created_at < :end
            """), {"tenant": tenant_id, "start": start, "end": end})
        ).mappings().one()

        total = int(row["total"] or 0)
        human = int(row["human"] or 0)
        ai_closed = int(row["ai_closed"] or 0)
        closed = int(row["closed"] or 0)
        has_reply = int(row["has_first_reply"] or 0)
        fast = int(row["fast_first_reply"] or 0)
        metrics["conversations"] = {
            "total": total,
            "closed": closed,
            "human_mode": human,
            "handoff_rate": round(human / total, 4) if total else 0.0,
            "ai_closed": ai_closed,
            "ai_resolution_rate": (
                round(ai_closed / closed, 4) if closed else 0.0
            ),
        }
        metrics["response"] = {
            "first_reply_within_60s": fast,
            "with_first_reply": has_reply,
            "rate": round(fast / has_reply, 4) if has_reply else 0.0,
        }

        # ── 满意度 ──
        row = (
            await db.execute(text("""
                SELECT count(*)                        AS rated,
                       avg(rating)                     AS avg_rating,
                       jsonb_object_agg(rating, cnt)   AS dist
                FROM (
                    SELECT rating, count(*) AS cnt
                    FROM customer_service.conversations
                    WHERE tenant_id = :tenant
                      AND rated_at >= :start AND rated_at < :end
                      AND rating IS NOT NULL
                    GROUP BY rating
                ) d
            """), {"tenant": tenant_id, "start": start, "end": end})
        ).mappings().one()
        rated = int(row["rated"] or 0)
        metrics["satisfaction"] = {
            "rated_count": rated,
            "avg_rating": (
                round(float(row["avg_rating"]), 3) if row["avg_rating"] else None
            ),
            "distribution": {
                str(k): int(v) for k, v in (row["dist"] or {}).items()
            },
        }

        # ── 意图 Top10 ──
        rows = (
            await db.execute(text("""
                SELECT intent_name, count(*) AS cnt
                FROM customer_service.messages
                WHERE role = 'user'
                  AND created_at >= :start AND created_at < :end
                  AND intent_name IS NOT NULL AND intent_name <> ''
                GROUP BY intent_name
                ORDER BY cnt DESC
                LIMIT 10
            """), {"start": start, "end": end})
        ).all()
        metrics["top_intents"] = [
            {"intent": r[0], "count": int(r[1])} for r in rows
        ]

        # ── 坐席维度 ──
        rows = (
            await db.execute(text("""
                SELECT h.assigned_agent_id,
                       count(DISTINCT h.conversation_id)                    AS handled,
                       round(avg(c.rating)::numeric, 3)                     AS avg_rating
                FROM customer_service.handoffs h
                LEFT JOIN customer_service.conversations c
                       ON c.conversation_id = h.conversation_id
                      AND c.rating IS NOT NULL
                WHERE h.tenant_id = :tenant
                  AND h.created_at >= :start AND h.created_at < :end
                  AND h.assigned_agent_id IS NOT NULL
                GROUP BY h.assigned_agent_id
                ORDER BY handled DESC
                LIMIT 20
            """), {"tenant": tenant_id, "start": start, "end": end})
        ).all()
        metrics["agents"] = [
            {
                "agent_id": r[0],
                "handled": int(r[1]),
                "avg_rating": float(r[2]) if r[2] is not None else None,
            }
            for r in rows
        ]

        # ── 工单维度（批次C）──
        row = (
            await db.execute(text("""
                SELECT count(*)                                       AS new_total,
                       count(*) FILTER (WHERE status <> 'closed')     AS open_total,
                       count(*) FILTER (WHERE created_at < now() - interval '48 hours'
                                          AND status NOT IN ('resolved', 'closed'))
                                                                       AS stale_48h
                FROM customer_service.tickets
                WHERE tenant_id = :tenant
                  AND created_at >= :start AND created_at < :end
            """), {"tenant": tenant_id, "start": start, "end": end})
        ).mappings().one()
        rows = (
            await db.execute(text("""
                SELECT type, count(*) AS cnt
                FROM customer_service.tickets
                WHERE tenant_id = :tenant
                  AND created_at >= :start AND created_at < :end
                GROUP BY type
            """), {"tenant": tenant_id, "start": start, "end": end})
        ).all()
        metrics["tickets"] = {
            "new_total": int(row["new_total"] or 0),
            "open_total": int(row["open_total"] or 0),
            "stale_over_48h": int(row["stale_48h"] or 0),
            "by_type": {r[0]: int(r[1]) for r in rows},
        }

        # ── FAQ 双轨周聚合（C5/G，2026-10-04）──
        # 滚动 7 天口径与 faq.stats() 一致；top_miss 是缺口闭环（C6）的
        # 周检输入，随日报产出后无需再手拉 ai.cs_faq_query_log
        row = (
            await db.execute(text("""
                SELECT count(*)                                              AS queries_7d,
                       count(*) FILTER (WHERE matched)                       AS hits_7d,
                       coalesce(round(avg(latency_ms) FILTER (WHERE matched)), 0)
                                                                             AS avg_hit_latency
                FROM ai.cs_faq_query_log
                WHERE created_at >= now() - interval '7 days'
            """))
        ).mappings().one()
        miss_rows = (
            await db.execute(text("""
                SELECT question, count(*) AS cnt
                FROM ai.cs_faq_query_log
                WHERE matched = false
                  AND created_at >= now() - interval '7 days'
                GROUP BY question
                ORDER BY cnt DESC, question
                LIMIT 10
            """))
        ).all()
        published = (
            await db.execute(text(
                "SELECT count(*) FROM ai.cs_faq WHERE status = 'published'"
            ))
        ).scalar()
        queries_7d = int(row["queries_7d"] or 0)
        hits_7d = int(row["hits_7d"] or 0)
        metrics["faq"] = {
            "published": int(published or 0),
            "queries_7d": queries_7d,
            "hits_7d": hits_7d,
            "hit_ratio_7d": round(hits_7d / queries_7d, 4) if queries_7d else None,
            "avg_hit_latency_ms": int(row["avg_hit_latency"] or 0),
            "top_miss": [{"question": r[0], "count": int(r[1])} for r in miss_rows],
        }

    return metrics


async def _upsert_report(
    report_date: date, tenant_id: str, metrics: dict[str, Any],
) -> None:
    from backend.memory.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db, db.begin():
        await db.execute(text("""
            INSERT INTO customer_service.qa_daily_reports
                (report_date, tenant_id, metrics, generated_at)
            VALUES (:report_date, :tenant, CAST(:metrics AS jsonb), now())
            ON CONFLICT (tenant_id, report_date)
            DO UPDATE SET metrics = CAST(:metrics AS jsonb),
                          generated_at = now()
        """), {
            "report_date": report_date,
            "tenant": tenant_id,
            "metrics": json.dumps(metrics, ensure_ascii=False),
        })


async def generate_daily_report(
    report_date: date | None = None, tenant_id: str = "default",
) -> dict[str, Any]:
    """生成并落库指定日期报表；返回 {"ok", "report_date", "metrics"| "error"}。"""
    report_date = report_date or (datetime.now(timezone.utc) - timedelta(days=1)).date()
    try:
        metrics = await _collect_metrics(report_date, tenant_id)
        await _upsert_report(report_date, tenant_id, metrics)
    except Exception as exc:
        logger.error(
            "[QAReport] 日报生成失败 date=%s: %s", report_date, exc,
            exc_info=True,
        )
        return {"ok": False, "error": str(exc), "report_date": report_date.isoformat()}

    _record_prometheus_safe(metrics)
    logger.info("[QAReport] 日报完成 date=%s tenant=%s", report_date, tenant_id)
    return {"ok": True, "report_date": report_date.isoformat(), "metrics": metrics}


async def list_reports(
    start: date, end: date, tenant_id: str = "default", limit: int = 31,
) -> list[dict[str, Any]]:
    from backend.memory.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(text("""
                SELECT report_date, metrics, generated_at
                FROM customer_service.qa_daily_reports
                WHERE tenant_id = :tenant
                  AND report_date >= :start AND report_date <= :end
                ORDER BY report_date DESC
                LIMIT :limit
            """), {
                "tenant": tenant_id, "start": start, "end": end,
                "limit": limit,
            })
        ).all()
        return [
            {
                "report_date": r[0].isoformat(),
                "metrics": r[1] if isinstance(r[1], dict) else json.loads(r[1]),
                "generated_at": r[2].isoformat() if r[2] else None,
            }
            for r in rows
        ]


def _record_prometheus_safe(metrics: dict[str, Any]) -> None:
    """Prometheus gauge 尽力而为（对齐 cs_dispatch_* 风格），失败仅告警。"""
    try:
        from backend.observability.metrics import (
            record_cs_qa_conversations,
            record_cs_qa_satisfaction,
        )
    except ImportError:
        return
    try:
        conversations = metrics.get("conversations") or {}
        record_cs_qa_conversations(conversations.get("total", 0),
                                   conversations.get("handoff_rate", 0.0))
        satisfaction = metrics.get("satisfaction") or {}
        record_cs_qa_satisfaction(satisfaction.get("avg_rating"))
        faq = metrics.get("faq") or {}
        if faq.get("hit_ratio_7d") is not None:
            cs_qa_daily_faq_hit_ratio.set(float(faq["hit_ratio_7d"]))
        if faq.get("published") is not None:
            cs_qa_daily_faq_published.set(max(0, int(faq["published"])))
    except Exception:
        logger.warning("[QAReport] prometheus 记录失败", exc_info=True)


def run_daily_report(report_date: date | None = None) -> dict[str, Any]:
    """同步入口（Celery 薄壳 / 手动触发）。"""
    from backend.customer_service._db_loop import run_sync

    return run_sync(generate_daily_report(report_date))


# =============================================
# G2/G3 告警数据源修复（2026-10-04 实机验收发现）：日报 gauge 的 app 进程
# 周期刷新。此前 gauge 只随日报执行（Celery beat/worker 进程）写值，而
# Prometheus 仅抓 app:8000 —— CsFaqHitRatioLow / CsFaqLayerFailureSpike
# 两条告警的数据源在 app 进程恒为空，规则加载后永不触发。本刷新器由 app
# startup 拉起（daemon 线程），从 customer_service.qa_daily_reports 拉最新
# 日报的 metrics.faq 段刷 gauge；日报批处理进程照旧直写（multiproc=max
# 聚合取两边最大值，语义一致）。
# =============================================
_GAUGE_REFRESH_INTERVAL_S = 600.0


def refresh_faq_gauges_from_store() -> dict | None:
    """从日报表拉最新 faq 段刷 gauge（app 进程告警数据源）。软失败。"""
    try:
        from backend.customer_service._db_loop import run_sync

        async def _pull():
            from sqlalchemy import text
            from backend.memory.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                row = await db.execute(text(
                    "SELECT metrics->'faq' FROM customer_service.qa_daily_reports "
                    "WHERE metrics ? 'faq' ORDER BY report_date DESC LIMIT 1"))
                r = row.first()
                return r[0] if r else None

        faq = run_sync(_pull())
        if not faq:
            return None
        ratio = faq.get("hit_ratio_7d")
        published = faq.get("published")
        if ratio is not None:
            cs_qa_daily_faq_hit_ratio.set(float(ratio))
        if published is not None:
            cs_qa_daily_faq_published.set(float(published))
        return {"hit_ratio": ratio, "published": published}
    except Exception as exc:  # noqa: BLE001 - 旁路刷新失败不影响主链
        logger.warning("[QAReport] faq gauge 刷新失败（忽略）: %s", exc)
        return None


def start_faq_gauge_refresher(interval_s: float = _GAUGE_REFRESH_INTERVAL_S) -> None:
    """启动 app 进程内的 gauge 周期刷新 daemon 线程（幂等）。"""
    import threading

    def _loop():
        import time as _time
        while True:
            refresh_faq_gauges_from_store()
            _time.sleep(interval_s)

    t = threading.Thread(target=_loop, name="faq-gauge-refresher", daemon=True)
    t.start()
    logger.info("[QAReport] faq gauge refresher started (interval=%ss)",
                interval_s)
