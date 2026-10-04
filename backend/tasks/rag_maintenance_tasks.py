"""RAG 维护任务：索引对账与解析超时看门狗。"""

from __future__ import annotations

from typing import Any

from backend.tasks.celery_app import celery_app


@celery_app.task(name="rag.parsing_timeout_watchdog")
def parsing_timeout_watchdog() -> dict[str, Any]:
    """每 15 分钟收敛超过一小时的 parsing 记录，动作幂等且按路径锁定。"""
    from backend.config.database import DOC_REGISTRY_PG_CONFIG, DOC_REGISTRY_PG_TABLE
    from backend.infra.db import engine_for
    from backend.observability.metrics import agent_rag_parsing_timeout_total
    from psycopg2 import sql
    from psycopg2.extras import RealDictCursor

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
    return {"changed_row_count": changed, "selected_row_count": len(rows)}


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
