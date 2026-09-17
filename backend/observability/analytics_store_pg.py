"""PostgresAnalyticsStore — analytics（trace_summary）的 PostgreSQL 连接层。

与 SQLite 版 `AnalyticsStore` 对外接口完全一致（save/save_dict/list/sessions/
cost_summary/count/enabled），仅替换连接层与 SQL 方言。引擎开关见
PG 为唯一实现（2026-09-17 SQLite 轨删除）。
工厂分发见 `analytics_store.py::get_analytics_store`。

方言映射要点：
  - `INSERT OR REPLACE` → `INSERT ... ON CONFLICT (trace_id) DO UPDATE`
  - `substr(ts, 1, 10)` PG 原生支持，聚合 SQL 无需改写
  - sqlite3.Row → psycopg2 RealDictCursor（_row_to_dict 语义不变）
软失败原则同 SQLite 版：写入/查询失败只记日志，绝不向上抛异常。
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg2
import psycopg2.extras

from backend.config.database import OBS_DB_PG_CONFIG
from backend.observability.analytics_store import (
    _MAX_ROWS,
    AnalyticsStore,
    _cfg_enabled,
)
from backend.shared.logger import logger


class PostgresAnalyticsStore(AnalyticsStore):
    """trace_summary 结构化存储的 PostgreSQL 实现（AnalyticsStore 子类）。"""

    def __init__(self, db_path: str = "data/analytics.db"):
        self._db_path = db_path  # 兼容保留，PG 模式下无意义
        self._lock = threading.Lock()
        self._table = os.getenv("OBS_DB_PG_TABLE_PREFIX", "") + "trace_summary"
        self._init_db()

    # ---- 连接层 ----

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        conn = psycopg2.connect(**OBS_DB_PG_CONFIG)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _exec(self, conn: Any, sql: str, params: tuple = ()) -> Any:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        return cur

    def _exec_scalar(self, conn: Any, sql: str, params: tuple = ()) -> Any:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur

    # ---- 建表（幂等，与 backend/sql/migrations/013_obs_analytics_pg.sql 一致）----

    def _init_db(self):
        t = self._table
        with self._lock, self._conn() as conn:
            conn.cursor().execute(f"""
                CREATE TABLE IF NOT EXISTS {t} (
                    trace_id          TEXT PRIMARY KEY,
                    ts                TEXT NOT NULL,
                    session_id        TEXT NOT NULL DEFAULT '',
                    workflow_name     TEXT NOT NULL DEFAULT '',
                    workflow_kind     TEXT NOT NULL DEFAULT 'other',
                    status            TEXT NOT NULL DEFAULT 'success',
                    question          TEXT NOT NULL DEFAULT '',
                    answer_preview    TEXT NOT NULL DEFAULT '',
                    duration_ms       INTEGER NOT NULL DEFAULT 0,
                    model             TEXT NOT NULL DEFAULT '',
                    provider          TEXT NOT NULL DEFAULT '',
                    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens      INTEGER NOT NULL DEFAULT 0,
                    cost_usd          DOUBLE PRECISION NOT NULL DEFAULT 0,
                    kb_id             TEXT NOT NULL DEFAULT '',
                    rejected          INTEGER NOT NULL DEFAULT 0,
                    tags              TEXT NOT NULL DEFAULT '{{}}',
                    created_at        TEXT NOT NULL
                )
            """)
            cur = conn.cursor()
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_session "
                        f"ON {t}(session_id, ts DESC)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_ts ON {t}(ts DESC)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_workflow "
                        f"ON {t}(workflow_name, ts DESC)")

    # ---- 写入 ----

    def save_dict(self, data: dict) -> bool:
        """接受已序列化的 trace dict 写入（供 trace_writer 异步路径调用）。"""
        if not _cfg_enabled():
            return False
        try:
            usage = data.get("usage") or {}
            tags = data.get("tags") or {}
            metadata = data.get("metadata") or {}
            rejection = metadata.get("rejection") or {}
            spans = data.get("spans") or []
            cost = sum(
                (s.get("metrics") or {}).get("cost_usd", 0)
                for s in spans if isinstance(s, dict)
            ) or (data.get("cost") or {}).get("total_usd", 0) or 0

            now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            with self._lock, self._conn() as conn:
                self._exec(conn, f"""
                    INSERT INTO {self._table} (
                        trace_id, ts, session_id, workflow_name, workflow_kind,
                        status, question, answer_preview, duration_ms,
                        model, provider, prompt_tokens, completion_tokens,
                        total_tokens, cost_usd, kb_id, rejected, tags, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (trace_id) DO UPDATE SET
                        ts = EXCLUDED.ts, session_id = EXCLUDED.session_id,
                        workflow_name = EXCLUDED.workflow_name,
                        workflow_kind = EXCLUDED.workflow_kind,
                        status = EXCLUDED.status, question = EXCLUDED.question,
                        answer_preview = EXCLUDED.answer_preview,
                        duration_ms = EXCLUDED.duration_ms, model = EXCLUDED.model,
                        provider = EXCLUDED.provider,
                        prompt_tokens = EXCLUDED.prompt_tokens,
                        completion_tokens = EXCLUDED.completion_tokens,
                        total_tokens = EXCLUDED.total_tokens,
                        cost_usd = EXCLUDED.cost_usd, kb_id = EXCLUDED.kb_id,
                        rejected = EXCLUDED.rejected, tags = EXCLUDED.tags,
                        created_at = EXCLUDED.created_at
                """, (
                    data.get("id", ""),
                    data.get("timestamp", "") or now,
                    data.get("session_id", "") or "",
                    data.get("workflow_name", "") or "",
                    data.get("workflow_kind", "other") or "other",
                    data.get("status", "success") or "success",
                    data.get("question", "") or "",
                    data.get("answer_preview", "") or "",
                    int(data.get("total_ms", 0) or data.get("duration_ms", 0) or 0),
                    data.get("model", "") or "",
                    data.get("provider", "") or "",
                    int(usage.get("prompt_tokens", 0) or 0),
                    int(usage.get("completion_tokens", 0) or 0),
                    int(usage.get("total_tokens", 0) or 0),
                    float(cost),
                    str(tags.get("kb_id", "") or metadata.get("kb_id", "") or ""),
                    1 if rejection.get("rejected") else 0,
                    json.dumps(tags, ensure_ascii=False, default=str),
                    now,
                ))
                count = self._exec_scalar(
                    conn, f"SELECT COUNT(*) FROM {self._table}").fetchone()[0]
                if count > _MAX_ROWS:
                    conn.cursor().execute(
                        f"DELETE FROM {self._table} WHERE trace_id IN "
                        f"(SELECT trace_id FROM {self._table} "
                        f"ORDER BY created_at ASC LIMIT %s)",
                        (count - _MAX_ROWS + 200,),
                    )
            return True
        except Exception as e:
            logger.warning(f"[AnalyticsStore-PG] save_dict 失败 "
                           f"{data.get('id', '?')}: {e}")
            return False

    # save(record) 继承：_record_to_dict → save_dict，语义不变

    # ---- 查询 ----

    def list(self, limit: int = 50, workflow_name: str | None = None,
             session_id: str | None = None) -> list[dict]:
        try:
            sql = f"SELECT * FROM {self._table}"
            where, params = [], []
            if workflow_name:
                where.append("workflow_name = %s")
                params.append(workflow_name)
            if session_id:
                where.append("session_id = %s")
                params.append(session_id)
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY ts DESC LIMIT %s"
            params.append(limit)
            with self._lock, self._conn() as conn:
                rows = self._exec(conn, sql, tuple(params)).fetchall()
            return [self._row_to_dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"[AnalyticsStore-PG] list 失败: {e}")
            return []

    def sessions(self, limit: int = 50) -> list[dict]:
        try:
            with self._lock, self._conn() as conn:
                rows = self._exec(conn, f"""
                    SELECT session_id,
                           COUNT(*)            AS turns,
                           MAX(ts)             AS last_ts,
                           MIN(ts)             AS first_ts,
                           AVG(duration_ms)    AS avg_duration_ms,
                           SUM(total_tokens)   AS total_tokens,
                           SUM(cost_usd)       AS total_cost_usd,
                           SUM(rejected)       AS rejected_count
                    FROM {self._table}
                    WHERE session_id != ''
                    GROUP BY session_id
                    ORDER BY last_ts DESC
                    LIMIT %s
                """, (limit,)).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"[AnalyticsStore-PG] sessions 失败: {e}")
            return []

    def cost_summary(self, days: int = 7) -> list[dict]:
        try:
            cutoff = time.strftime(
                "%Y-%m-%d 00:00:00",
                time.localtime(time.time() - days * 86400),
            )
            with self._lock, self._conn() as conn:
                rows = self._exec(conn, f"""
                    SELECT substr(ts, 1, 10)  AS day,
                           model,
                           COUNT(*)           AS traces,
                           SUM(prompt_tokens)     AS prompt_tokens,
                           SUM(completion_tokens) AS completion_tokens,
                           SUM(total_tokens)      AS total_tokens,
                           SUM(cost_usd)          AS total_cost_usd
                    FROM {self._table}
                    WHERE created_at >= %s
                    GROUP BY substr(ts, 1, 10), model
                    ORDER BY day DESC
                """, (cutoff,)).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"[AnalyticsStore-PG] cost_summary 失败: {e}")
            return []

    def count(self) -> int:
        try:
            with self._lock, self._conn() as conn:
                return self._exec_scalar(
                    conn, f"SELECT COUNT(*) FROM {self._table}").fetchone()[0]
        except Exception:
            return 0
