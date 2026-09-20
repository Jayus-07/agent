"""RAG 处理血缘 PostgreSQL 仓储。"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

from backend.config.database import (
    DB_POOL_MAX_CONN,
    DB_POOL_MIN_CONN,
    RAG_STORES_PG_CONFIG,
    RAG_STORES_PG_TABLE_PREFIX,
)
from backend.rag.indexing.processing_lineage import (
    ProcessingRunSnapshot,
    ProcessingStepSnapshot,
)
from backend.shared.logger import logger


def _json_value(value: Any) -> Any:
    """将内存字典包装为 psycopg2 JSON 参数；None 保持 None。"""

    return psycopg2.extras.Json(value) if value is not None else None


def _iso(value: datetime | str | None) -> datetime | str | None:
    return value.isoformat() if isinstance(value, datetime) else value


def _percentile(values: list[float], quantile: float) -> float:
    """计算连接池等待样本的 nearest-rank P95。"""

    if not values:
        return 0.0
    ordered = sorted(max(float(value), 0.0) for value in values)
    index = max(int(len(ordered) * quantile + 0.999999), 1) - 1
    return ordered[min(index, len(ordered) - 1)]


class PostgresProcessingLineageRepository:
    """处理运行和阶段的 PG 存储，写入失败由调用方决定是否降级。"""

    # 索引主链路通过 ProcessingRunRecorder 的单写线程异步落库，避免每个
    # stage 都同步占用 worker 线程；写入和查询共享进程内有界连接池。
    non_blocking = True

    def __init__(self) -> None:
        prefix = os.getenv("RAG_STORES_PG_TABLE_PREFIX", RAG_STORES_PG_TABLE_PREFIX)
        self._runs_table = f"{prefix}rag_processing_runs"
        self._steps_table = f"{prefix}rag_processing_steps"
        self._pool: ThreadedConnectionPool | None = None
        self._pool_lock = threading.Lock()
        self._pool_min_conn = max(int(DB_POOL_MIN_CONN), 1)
        self._pool_max_conn = max(
            int(DB_POOL_MAX_CONN), self._pool_min_conn
        )
        self._pool_wait_samples: deque[float] = deque(maxlen=10000)
        self._pool_wait_lock = threading.Lock()

    def _get_pool(self) -> ThreadedConnectionPool:
        """惰性创建进程内线程安全连接池。"""

        pool = self._pool
        if pool is not None:
            return pool
        with self._pool_lock:
            if self._pool is None:
                self._pool = ThreadedConnectionPool(
                    self._pool_min_conn,
                    self._pool_max_conn,
                    **RAG_STORES_PG_CONFIG,
                )
            return self._pool

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        pool = self._get_pool()
        wait_started = time.monotonic()
        conn = pool.getconn()
        with self._pool_wait_lock:
            self._pool_wait_samples.append(
                max((time.monotonic() - wait_started) * 1000, 0.0)
            )
        close_connection = False
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                # 网络断开时 rollback 也可能失败，不能把坏连接放回池里。
                close_connection = True
            raise
        finally:
            pool.putconn(conn, close=close_connection)

    def pool_wait_stats(self) -> dict[str, float | int]:
        """返回本进程连接池借用等待的最近窗口统计。"""

        with self._pool_wait_lock:
            samples = list(self._pool_wait_samples)
        return {
            "sample_count": len(samples),
            "p95_ms": round(_percentile(samples, 0.95), 3),
            "max_ms": round(max(samples, default=0.0), 3),
        }

    def count_duplicate_stage_keys(self, run_ids: list[str]) -> int:
        """统计指定运行的重复阶段键；唯一约束正常时应始终为零。"""

        if not run_ids:
            return 0
        with self._conn() as conn:
            row = self._exec(
                conn,
                f"""
                SELECT COALESCE(SUM(group_count - 1), 0) AS duplicate_count
                  FROM (
                    SELECT run_id, stage, attempt_no, COUNT(*) AS group_count
                      FROM {self._steps_table}
                     WHERE run_id = ANY(%s)
                     GROUP BY run_id, stage, attempt_no
                    HAVING COUNT(*) > 1
                  ) duplicate_groups
                """,
                (run_ids,),
            ).fetchone()
        return int((row or {}).get("duplicate_count", 0) or 0)

    def _exec(self, conn: Any, sql: str, params: tuple | list = ()) -> Any:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(sql, params)
        return cursor

    def create_run(self, snapshot: ProcessingRunSnapshot) -> str:
        """写入运行快照；重复 run_id 只保留首次创建的时间和身份。"""

        with self._conn() as conn:
            self._exec(
                conn,
                f"""
                INSERT INTO {self._runs_table} (
                    run_id, doc_id, file_hash, operation, status,
                    pipeline_version, git_sha, config_snapshot_hash,
                    config_snapshot, task_id, batch_id, trace_id,
                    started_at, finished_at, error_message, model_summary
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                ON CONFLICT (run_id) DO NOTHING
                """,
                (
                    snapshot.run_id,
                    snapshot.doc_id,
                    snapshot.file_hash,
                    snapshot.operation,
                    snapshot.status,
                    snapshot.pipeline_version,
                    snapshot.git_sha,
                    snapshot.config_snapshot_hash,
                    _json_value(snapshot.config_snapshot),
                    snapshot.task_id,
                    snapshot.batch_id,
                    snapshot.trace_id,
                    _iso(snapshot.started_at),
                    _iso(snapshot.finished_at),
                    snapshot.error_message,
                    _json_value(snapshot.model_summary),
                ),
            )
        return snapshot.run_id

    def upsert_stage(self, snapshot: ProcessingStepSnapshot) -> None:
        """按运行、阶段和尝试号幂等写入阶段快照。"""

        with self._conn() as conn:
            self._exec(
                conn,
                f"""
                INSERT INTO {self._steps_table} (
                    step_id, run_id, stage, ordinal, attempt_no, status,
                    role, engine_type, provider, model_name, model_revision,
                    config_source, config_revision, artifact_fingerprint,
                    prompt_key, prompt_version, prompt_hash,
                    taxonomy_version, rules_version, schema_fingerprint,
                    cache_status, input_count, output_count,
                    prompt_tokens, completion_tokens, total_tokens,
                    cached_tokens, reasoning_tokens, cost_usd, duration_ms,
                    retry_count, fallback_reason, skip_reason, error_message,
                    started_at, finished_at, metadata
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (run_id, stage, attempt_no) DO UPDATE SET
                    step_id = EXCLUDED.step_id,
                    ordinal = EXCLUDED.ordinal,
                    status = EXCLUDED.status,
                    role = EXCLUDED.role,
                    engine_type = EXCLUDED.engine_type,
                    provider = EXCLUDED.provider,
                    model_name = EXCLUDED.model_name,
                    model_revision = EXCLUDED.model_revision,
                    config_source = EXCLUDED.config_source,
                    config_revision = EXCLUDED.config_revision,
                    artifact_fingerprint = EXCLUDED.artifact_fingerprint,
                    prompt_key = EXCLUDED.prompt_key,
                    prompt_version = EXCLUDED.prompt_version,
                    prompt_hash = EXCLUDED.prompt_hash,
                    taxonomy_version = EXCLUDED.taxonomy_version,
                    rules_version = EXCLUDED.rules_version,
                    schema_fingerprint = EXCLUDED.schema_fingerprint,
                    cache_status = EXCLUDED.cache_status,
                    input_count = EXCLUDED.input_count,
                    output_count = EXCLUDED.output_count,
                    prompt_tokens = EXCLUDED.prompt_tokens,
                    completion_tokens = EXCLUDED.completion_tokens,
                    total_tokens = EXCLUDED.total_tokens,
                    cached_tokens = EXCLUDED.cached_tokens,
                    reasoning_tokens = EXCLUDED.reasoning_tokens,
                    cost_usd = EXCLUDED.cost_usd,
                    duration_ms = EXCLUDED.duration_ms,
                    retry_count = EXCLUDED.retry_count,
                    fallback_reason = EXCLUDED.fallback_reason,
                    skip_reason = EXCLUDED.skip_reason,
                    error_message = EXCLUDED.error_message,
                    started_at = EXCLUDED.started_at,
                    finished_at = EXCLUDED.finished_at,
                    metadata = EXCLUDED.metadata
                """,
                (
                    snapshot.step_id,
                    snapshot.run_id,
                    snapshot.stage,
                    snapshot.ordinal,
                    snapshot.attempt_no,
                    snapshot.status,
                    snapshot.role,
                    snapshot.engine_type,
                    snapshot.provider,
                    snapshot.model_name,
                    snapshot.model_revision,
                    snapshot.config_source,
                    snapshot.config_revision,
                    snapshot.artifact_fingerprint,
                    snapshot.prompt_key,
                    snapshot.prompt_version,
                    snapshot.prompt_hash,
                    snapshot.taxonomy_version,
                    snapshot.rules_version,
                    snapshot.schema_fingerprint,
                    snapshot.cache_status,
                    snapshot.input_count,
                    snapshot.output_count,
                    snapshot.prompt_tokens,
                    snapshot.completion_tokens,
                    snapshot.total_tokens,
                    snapshot.cached_tokens,
                    snapshot.reasoning_tokens,
                    snapshot.cost_usd,
                    snapshot.duration_ms,
                    snapshot.retry_count,
                    snapshot.fallback_reason,
                    snapshot.skip_reason,
                    snapshot.error_message,
                    _iso(snapshot.started_at),
                    _iso(snapshot.finished_at),
                    _json_value(snapshot.metadata),
                ),
            )

    def finish_run(
        self,
        run_id: str,
        status: str,
        *,
        finished_at: datetime | str | None = None,
        error_message: str | None = None,
        model_summary: list[dict[str, Any]] | None = None,
    ) -> None:
        with self._conn() as conn:
            self._exec(
                conn,
                f"""
                UPDATE {self._runs_table}
                   SET status = %s,
                       finished_at = %s,
                       error_message = %s,
                       model_summary = %s
                 WHERE run_id = %s
                """,
                (
                    status,
                    _iso(finished_at),
                    error_message,
                    _json_value(model_summary or []),
                    run_id,
                ),
            )

    def get_latest_for_doc(self, doc_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = self._exec(
                conn,
                f"""
                SELECT * FROM {self._runs_table}
                 WHERE doc_id = %s AND status = 'success'
                 ORDER BY finished_at DESC NULLS LAST, started_at DESC
                 LIMIT 1
                """,
                (doc_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_runs(self, doc_id: str, *, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        page = max(int(page), 1)
        page_size = min(max(int(page_size), 1), 100)
        offset = (page - 1) * page_size
        with self._conn() as conn:
            total = self._exec(
                conn,
                f"SELECT COUNT(*) AS total FROM {self._runs_table} WHERE doc_id = %s",
                (doc_id,),
            ).fetchone()
            rows = self._exec(
                conn,
                f"""
                SELECT run_id, doc_id, file_hash, operation, status,
                       pipeline_version, config_snapshot_hash, task_id,
                       batch_id, trace_id, started_at, finished_at,
                       error_message, model_summary
                  FROM {self._runs_table}
                 WHERE doc_id = %s
                 ORDER BY started_at DESC
                 LIMIT %s OFFSET %s
                """,
                (doc_id, page_size, offset),
            ).fetchall()
        return {
            "items": [dict(row) for row in rows],
            "total": int((total or {}).get("total", 0)),
            "page": page,
            "page_size": page_size,
        }

    def get_run_detail(self, doc_id: str, run_id: str) -> dict[str, Any] | None:
        # 查询使用独立短连接，不持有实例级锁，避免高并发详情查询阻塞
        # 处理运行的异步阶段写入。
        with self._conn() as conn:
            run = self._exec(
                conn,
                f"SELECT * FROM {self._runs_table} WHERE run_id = %s AND doc_id = %s",
                (run_id, doc_id),
            ).fetchone()
            if not run:
                return None
            steps = self._exec(
                conn,
                f"""
                SELECT * FROM {self._steps_table}
                 WHERE run_id = %s
                 ORDER BY ordinal, attempt_no
                """,
                (run_id,),
            ).fetchall()
        result = dict(run)
        result["steps"] = [dict(step) for step in steps]
        return result


_repository: PostgresProcessingLineageRepository | None = None
_repository_lock = threading.Lock()


def get_processing_lineage_repository() -> PostgresProcessingLineageRepository:
    """返回进程内仓储单例，连接仍按操作短连接。"""

    global _repository
    if _repository is None:
        with _repository_lock:
            if _repository is None:
                _repository = PostgresProcessingLineageRepository()
    return _repository
