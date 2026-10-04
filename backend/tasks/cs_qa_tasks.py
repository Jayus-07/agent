"""tasks/cs_qa_tasks.py — 客服质检每日报表 beat 任务（批次D）。

薄壳：核心逻辑全部在 backend/customer_service/qa_report.py
（与 Celery 解耦，可测试、可手动触发）。本模块只做任务注册与日志包装。

调度：每日 06:10 UTC 由 celery_app.conf.beat_schedule 注册（crontab）。
幂等：qa_daily_reports 按 (tenant_id, report_date) 唯一，重复执行覆盖。
失败：报表失败显式抛错重试（日报缺失比迟到更危险），最多重试 3 次。
"""
from __future__ import annotations

from backend.config.tasks import (
    CELERY_MAX_RETRIES,
    CELERY_RETRY_BACKOFF,
    CELERY_RETRY_BACKOFF_MAX,
)
from backend.shared.logger import logger
from backend.tasks.celery_app import celery_app


@celery_app.task(
    bind=True,
    name="cs.qa_daily_report",
    acks_late=True,
    autoretry_for=(RuntimeError,),
    retry_backoff=CELERY_RETRY_BACKOFF,
    retry_backoff_max=CELERY_RETRY_BACKOFF_MAX,
    retry_jitter=True,
    max_retries=3,
)
def cs_qa_daily_report(self) -> dict:
    """聚合昨日客服运营指标 → qa_daily_reports（幂等覆盖）。"""
    from backend.customer_service.qa_report import run_daily_report

    result = run_daily_report()
    if not result.get("ok"):
        logger.error(
            "[CSQATask] 日报生成失败: %s", result.get("error"),
        )
        raise RuntimeError(f"QA daily report failed: {result.get('error')}")
    logger.info(
        "[CSQATask] 日报完成 date=%s", result.get("report_date"),
    )
    return {
        "report_date": result.get("report_date"),
        "metrics_keys": sorted((result.get("metrics") or {}).keys()),
    }


@celery_app.task(
    bind=True,
    name="cs.faq.gap_review",
    acks_late=True,
    autoretry_for=(RuntimeError,),
    retry_backoff=CELERY_RETRY_BACKOFF,
    retry_backoff_max=CELERY_RETRY_BACKOFF_MAX,
    retry_jitter=True,
    max_retries=3,
)
def cs_faq_gap_review(self) -> dict:
    """FAQ 缺口周检（A8a/C6）：只读清单 + JSON 归档（缺口闭环台账输入）。

    混线解锁后登记（原 scripts/cs_faq_gap_review.py 仅手动触发，
    A8 验收「beat 登记等混线解锁」即本任务）。
    """
    import psycopg2

    from backend.config.database import DOC_REGISTRY_PG_CONFIG
    from scripts.cs_faq_gap_review import collect_report

    conn = psycopg2.connect(**DOC_REGISTRY_PG_CONFIG)
    try:
        report = collect_report(conn, days=7, limit=50)
    finally:
        conn.rollback()
        conn.close()

    # 归档：data/cs_gap_reviews/ 周检 JSON（台账留痕）
    from pathlib import Path as _P
    import time as _time
    out_dir = _P("data/cs_gap_reviews")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"gap_review_{_time.strftime('%Y%m%d_%H%M%S')}.json"
    import json as _json
    out.write_text(_json.dumps(report, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    logger.info(
        "[CSQATask] FAQ 缺口周检完成 open=%s closed_now=%s 归档=%s",
        report.get("open_count"), report.get("closed_now_count"), out,
    )
    return {"open_count": report.get("open_count"),
            "closed_now_count": report.get("closed_now_count"),
            "archived": str(out)}
