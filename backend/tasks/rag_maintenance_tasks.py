"""RAG 维护任务：索引对账与解析超时看门狗。"""

from __future__ import annotations

from typing import Any

from backend.shared.logger import logger
from backend.tasks.celery_app import celery_app


def _reconcile_stale_rag_runs() -> dict[str, Any]:
    """复核过期索引和处理血缘，分别收口并保留可审计记录。"""
    from backend.config.tasks import (
        RAG_INDEX_RUN_STALE_SECONDS,
        TASK_PENDING_RECOVERY_MAX_AGE_SECONDS,
    )

    result: dict[str, Any] = {
        "threshold_seconds": RAG_INDEX_RUN_STALE_SECONDS,
        "unpublished_threshold_seconds": TASK_PENDING_RECOVERY_MAX_AGE_SECONDS,
        "index_runs_failed": 0,
        "processing_runs_failed": 0,
        "processing_steps_failed": 0,
        "errors": [],
    }
    try:
        from backend.rag.indexing.index_run_store_pg import get_index_run_store

        failed = get_index_run_store().reconcile_stale_active_runs(
            stale_after_seconds=RAG_INDEX_RUN_STALE_SECONDS,
            unpublished_after_seconds=TASK_PENDING_RECOVERY_MAX_AGE_SECONDS)
        result["index_runs_failed"] = len(failed)
    except Exception as exc:  # noqa: BLE001 — 独立报告，不阻断文档看门狗
        result["errors"].append(f"index_runs: {exc}")
        logger.warning("[RAGMaintenance] 索引运行收口失败: %s", exc)

    try:
        from backend.rag.indexing.processing_lineage_pg import (
            get_processing_lineage_repository,
        )

        counts = get_processing_lineage_repository().fail_stale_running_runs(
            stale_after_seconds=RAG_INDEX_RUN_STALE_SECONDS)
        result["processing_runs_failed"] = counts["run_count"]
        result["processing_steps_failed"] = counts["step_count"]
    except Exception as exc:  # noqa: BLE001 — 独立报告，不阻断文档看门狗
        result["errors"].append(f"processing_lineage: {exc}")
        logger.warning("[RAGMaintenance] 处理血缘收口失败: %s", exc)
    return result


@celery_app.task(name="rag.parsing_timeout_watchdog")
def parsing_timeout_watchdog() -> dict[str, Any]:
    """定期收敛解析超时及长时间没有更新的索引/处理运行。"""
    from backend.config.database import DOC_REGISTRY_PG_CONFIG, DOC_REGISTRY_PG_TABLE
    from backend.infra.db import engine_for
    from backend.observability.metrics import agent_rag_parsing_timeout_total
    from psycopg2 import sql
    from psycopg2.extras import RealDictCursor

    stale_run_result = _reconcile_stale_rag_runs()
    conn = engine_for(DOC_REGISTRY_PG_CONFIG).raw_connection()
    changed = 0
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                sql.SQL(
                    "SELECT file_path,doc_id,updated_at,quality_issues "
                    "FROM {} WHERE status='parsing' AND updated_at IS NOT NULL "
                    "AND updated_at::timestamp < (now() AT TIME ZONE 'UTC') "
                    "- interval '1 hour' FOR UPDATE SKIP LOCKED"
                ).format(sql.Identifier(DOC_REGISTRY_PG_TABLE))
            )
            rows = cursor.fetchall()
            for row in rows:
                cursor.execute(
                    sql.SQL(
                        "UPDATE {} SET status='failed', "
                        "processing_status='parsing_timeout_watchdog', "
                        "quality_issues=CASE "
                        "WHEN COALESCE(quality_issues,'')='' "
                        "THEN 'parsing_timeout_watchdog' "
                        "WHEN position('parsing_timeout_watchdog' "
                        "IN quality_issues)=0 "
                        "THEN quality_issues || ', parsing_timeout_watchdog' "
                        "ELSE quality_issues END, "
                        "updated_at=to_char(now() AT TIME ZONE 'UTC', "
                        "'YYYY-MM-DD HH24:MI:SS') "
                        "WHERE file_path=%s AND status='parsing'"
                    ).format(sql.Identifier(DOC_REGISTRY_PG_TABLE)),
                    (row["file_path"],),
                )
                changed += cursor.rowcount
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    if changed:
        agent_rag_parsing_timeout_total.inc(changed)
    return {"changed_row_count": changed, "selected_row_count": len(rows),
            "stale_run_reconciliation": stale_run_result}


@celery_app.task(name="rag.index_reconcile")
def index_reconcile() -> dict[str, Any]:
    """每日审计索引三方关系；数据源不可达通过指标明确暴露。"""
    from psycopg2.extras import Json

    from backend.config.database import DOC_REGISTRY_PG_CONFIG
    from backend.infra.db import engine_for
    from backend.observability.metrics import (
        agent_rag_reconcile_inconsistent, agent_rag_reconcile_source_available,
    )
    from backend.rag.indexing.reconcile import run_reconcile

    agent_rag_reconcile_source_available.set(0)
    result = run_reconcile()
    total = result["issue_count"] + len(result["bm25_missing_doc_ids"] or [])
    total += len(result["bm25_orphan_doc_ids"] or [])
    agent_rag_reconcile_inconsistent.set(total)
    conn = engine_for(DOC_REGISTRY_PG_CONFIG).raw_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO ai.rag_reconcile_reports "
                "(consistent,issue_count,bm25_status,detail) VALUES (%s,%s,%s,%s)",
                (result["consistent"], total, result["bm25_status"], Json(result)),
            )
        conn.commit()
    except Exception:
        # 数据库失败必须回滚并向 Celery 抛出，不能伪造成功报告。
        conn.rollback()
        raise
    finally:
        conn.close()
    agent_rag_reconcile_source_available.set(
        int(result["bm25_status"] == "available")
    )
    return result
