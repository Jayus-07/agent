"""PostgresLLMUsageStore — llm_usage 明细的 PostgreSQL 连接层。

与 SQLite 版 `LLMUsageStore` 对外接口完全一致（record/by_trace/list_calls/
dashboard），仅替换连接层与 SQL 方言。引擎开关见
PG 为唯一实现（2026-09-17 SQLite 轨删除）。
工厂分发见 `llm_usage_store.py::get_llm_usage_store`。

方言映射要点：
  - `INTEGER PRIMARY KEY AUTOINCREMENT` → `BIGSERIAL PRIMARY KEY`（id 语义不变，
    list_calls 的 ORDER BY id DESC 即插入序）
  - `substr(ts, 1, 10)` PG 原生支持
  - `COUNT(DISTINCT CASE WHEN trace_id != '' THEN trace_id END)`：
    CASE 空串返回 NULL，PG COUNT 自动忽略 NULL，语义一致
软失败原则同 SQLite 版：写入/查询失败只记日志，绝不向上抛异常。
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg2
import psycopg2.extras

from backend.config.database import OBS_DB_PG_CONFIG
from backend.observability.llm_usage_store import (
    _MAX_ROWS,
    LLMUsageStore,
    _cfg_enabled,
    _now_iso,
)
from backend.shared.logger import logger

_COLS = ("ts, trace_id, request_id, session_id, user_id, tenant_id, "
         "component, model, provider, "
         "prompt_tokens, completion_tokens, total_tokens, "
         "cached_tokens, reasoning_tokens, cost_usd, duration_ms, finish_reason, decision")


class PostgresLLMUsageStore(LLMUsageStore):
    """llm_usage 明细存储的 PostgreSQL 实现（LLMUsageStore 子类）。"""

    def __init__(self, db_path: str = "data/analytics.db"):
        self._db_path = db_path  # 兼容保留，PG 模式下无意义
        self._lock = threading.Lock()
        self._table = os.getenv("OBS_DB_PG_TABLE_PREFIX", "") + "llm_usage"
        self._write_count = 0
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
                    id                BIGSERIAL PRIMARY KEY,
                    ts                TEXT NOT NULL,
                    trace_id          TEXT NOT NULL DEFAULT '',
                    request_id        TEXT NOT NULL DEFAULT '',
                    session_id        TEXT NOT NULL DEFAULT '',
                    user_id           TEXT NOT NULL DEFAULT '',
                    tenant_id         TEXT NOT NULL DEFAULT '',
                    component         TEXT NOT NULL DEFAULT 'llm',
                    model             TEXT NOT NULL DEFAULT '',
                    provider          TEXT NOT NULL DEFAULT '',
                    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens      INTEGER NOT NULL DEFAULT 0,
                    cached_tokens     INTEGER NOT NULL DEFAULT 0,
                    reasoning_tokens  INTEGER NOT NULL DEFAULT 0,
                    cost_usd          DOUBLE PRECISION NOT NULL DEFAULT 0,
                    duration_ms       DOUBLE PRECISION NOT NULL DEFAULT 0,
                    finish_reason     TEXT NOT NULL DEFAULT '',
                    decision          TEXT NOT NULL DEFAULT 'primary',
                    created_at        TEXT NOT NULL
                )
            """)
            cur = conn.cursor()
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS request_id "
                "TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS user_id "
                "TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS tenant_id "
                "TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS decision "
                "TEXT NOT NULL DEFAULT 'primary'"
            )
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_ts ON {t}(ts)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_model ON {t}(model, ts)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_trace ON {t}(trace_id)")
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{t}_tenant_user_ts "
                f"ON {t}(tenant_id, user_id, ts DESC)"
            )
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_component "
                        f"ON {t}(component, ts)")

    # ---- 写入 ----

    def record(self, event: dict[str, Any]) -> bool:
        """写入单次调用用量（LLM / Embedding / Reranker）。失败只记日志返回 False。"""
        if not _cfg_enabled():
            return False
        try:
            now = _now_iso()
            component = event.get("component", "llm")
            with self._lock, self._conn() as conn:
                self._exec(conn, f"""
                    INSERT INTO {self._table} (
                        ts, trace_id, request_id, session_id, user_id, tenant_id,
                        component, model, provider,
                        prompt_tokens, completion_tokens, total_tokens,
                        cached_tokens, reasoning_tokens, cost_usd,
                        duration_ms, finish_reason, decision, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    event.get("timestamp") or now,
                    str(event.get("trace_id") or ""),
                    str(event.get("request_id") or ""),
                    str(event.get("session_id") or ""),
                    str(event.get("user_id") or ""),
                    str(event.get("tenant_id") or ""),
                    str(component),
                    str(event.get("model") or ""),
                    str(event.get("provider") or ""),
                    int(event.get("prompt_tokens") or 0),
                    int(event.get("completion_tokens") or 0),
                    int(event.get("total_tokens") or 0),
                    int(event.get("cached_tokens") or 0),
                    int(event.get("reasoning_tokens") or 0),
                    float(event.get("cost_usd") or 0.0),
                    float(event.get("duration_ms") or 0.0),
                    str(event.get("finish_reason") or ""),
                    str(event.get("decision") or "primary"),
                    now,
                ))
                self._write_count += 1
                # 每 2000 次写入做一次裁剪检查（语义同 SQLite 版）
                if self._write_count % 2000 == 0:
                    count = self._exec_scalar(
                        conn, f"SELECT COUNT(*) FROM {self._table}").fetchone()[0]
                    if count > _MAX_ROWS:
                        conn.cursor().execute(
                            f"DELETE FROM {self._table} WHERE id IN "
                            f"(SELECT id FROM {self._table} ORDER BY id ASC LIMIT %s)",
                            (count - _MAX_ROWS,),
                        )
            return True
        except Exception as e:
            logger.warning(f"[LLMUsageStore-PG] 写入失败: {e}")
            return False

    # ---- 查询 ----

    def by_trace(self, trace_id: str, limit: int = 200) -> list[dict[str, Any]]:
        if not trace_id:
            return []
        try:
            with self._lock, self._conn() as conn:
                rows = self._exec(
                    conn,
                    f"""SELECT {_COLS}
                       FROM {self._table} WHERE trace_id = %s ORDER BY ts ASC LIMIT %s""",
                    (trace_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"[LLMUsageStore-PG] by_trace({trace_id}) 查询失败: {e}")
            return []

    def list_calls(self, days: int = 7, model: str | None = None,
                   component: str | None = None,
                   limit: int = 20, offset: int = 0) -> dict:
        try:
            cutoff = self._cutoff_iso(days)
            where, params = ["ts >= %s"], [cutoff]
            if model:
                where.append("model = %s")
                params.append(model)
            if component and component != "all":
                where.append("component = %s")
                params.append(component)
            where_sql = " AND ".join(where)
            with self._lock, self._conn() as conn:
                total = self._exec_scalar(
                    conn,
                    f"SELECT COUNT(*) FROM {self._table} WHERE {where_sql}",
                    tuple(params),
                ).fetchone()[0]
                rows = self._exec(
                    conn,
                    f"""SELECT {_COLS}
                       FROM {self._table} WHERE {where_sql}
                       ORDER BY id DESC LIMIT %s OFFSET %s""",
                    (*params, int(limit), int(offset)),
                ).fetchall()
            return {"calls": [dict(r) for r in rows], "total": total}
        except Exception as e:
            logger.warning(f"[LLMUsageStore-PG] list_calls 失败: {e}")
            return {"calls": [], "total": 0}

    def dashboard(self, days: int = 7, component: str | None = None) -> dict:
        empty = {
            "totals": {
                "requests": 0, "calls": 0,
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "cached_tokens": 0, "reasoning_tokens": 0, "cost_usd": 0.0,
            },
            "daily": [],
            "models": [],
        }
        try:
            cutoff = self._cutoff_iso(days)
            where_base = ["ts >= %s"]
            params_base = [cutoff]
            if component and component != "all":
                where_base.append("component = %s")
                params_base.append(component)
            where_sql = " AND ".join(where_base)

            with self._lock, self._conn() as conn:
                # ① 总量（requests = 去重轮次；calls = 调用次数）
                totals = dict(self._exec(conn, f"""
                    SELECT COUNT(DISTINCT CASE WHEN trace_id != '' THEN trace_id END) AS requests,
                           COUNT(*) AS calls,
                           COALESCE(SUM(prompt_tokens), 0)     AS prompt_tokens,
                           COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                           COALESCE(SUM(total_tokens), 0)      AS total_tokens,
                           COALESCE(SUM(cached_tokens), 0)     AS cached_tokens,
                           COALESCE(SUM(reasoning_tokens), 0)  AS reasoning_tokens,
                           COALESCE(SUM(cost_usd), 0)          AS cost_usd
                    FROM {self._table} WHERE {where_sql}
                """, tuple(params_base)).fetchone())

                # ② 日趋势（日 × 输入/输出/成本）
                daily = [dict(r) for r in self._exec(conn, f"""
                    SELECT substr(ts, 1, 10)             AS day,
                           COUNT(*)                      AS calls,
                           COALESCE(SUM(prompt_tokens), 0)     AS prompt_tokens,
                           COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                           COALESCE(SUM(total_tokens), 0)      AS total_tokens,
                           COALESCE(SUM(cost_usd), 0)          AS cost_usd
                    FROM {self._table} WHERE {where_sql}
                    GROUP BY substr(ts, 1, 10)
                    ORDER BY day ASC
                """, tuple(params_base)).fetchall()]

                # ③ 按模型细分（Provider/Model 维度）
                models = [dict(r) for r in self._exec(conn, f"""
                    SELECT provider, model,
                           COUNT(*) AS calls,
                           COUNT(DISTINCT CASE WHEN trace_id != '' THEN trace_id END) AS requests,
                           COALESCE(SUM(prompt_tokens), 0)     AS prompt_tokens,
                           COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                           COALESCE(SUM(total_tokens), 0)      AS total_tokens,
                           COALESCE(SUM(cached_tokens), 0)     AS cached_tokens,
                           COALESCE(SUM(reasoning_tokens), 0)  AS reasoning_tokens,
                           COALESCE(SUM(cost_usd), 0)          AS cost_usd
                    FROM {self._table} WHERE {where_sql}
                    GROUP BY provider, model
                    ORDER BY total_tokens DESC
                """, tuple(params_base)).fetchall()]

            totals["cost_usd"] = round(totals.get("cost_usd", 0) or 0, 6)
            for d in daily:
                d["cost_usd"] = round(d.get("cost_usd", 0) or 0, 6)
            for m in models:
                m["cost_usd"] = round(m.get("cost_usd", 0) or 0, 6)
            return {"totals": totals, "daily": daily, "models": models}
        except Exception as e:
            logger.warning(f"[LLMUsageStore-PG] dashboard 聚合失败: {e}")
            return empty
