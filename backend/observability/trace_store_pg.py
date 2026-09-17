"""PostgresTraceStore — trace_store 的 PostgreSQL 连接层（2026-09-17 迁移计划 Batch A）。

与 SQLite 版 `TraceStore` 对外接口完全一致（save/save_dict/get/list/list_since），
仅替换连接层与 SQL 方言。引擎开关见 `backend/config/database.py::OBS_DB_BACKEND`
（env `OBS_DB_BACKEND=postgres` 启用；默认 sqlite，即回滚开关）。
工厂分发见 `trace_store.py::get_trace_store`。

方言映射（SQLite → PostgreSQL）：
  - `?` 占位符          → `%s`
  - `INSERT OR REPLACE` → `INSERT ... ON CONFLICT (trace_id) DO UPDATE`
  - `json_extract(data, '$.metadata.rejection.rejected')` 回填
                        → 建表即带 rejected 摘要列，无需 JSON1（写入时同源提取）
  - sqlite3 隐式事务    → psycopg2 显式 commit/rollback（每次操作用完即关连接）

时间戳语义：created_at 由应用侧生成（time.localtime 文本，与 SQLite 版逐字一致），
不使用 PG 服务器时钟 —— list_since 的 cutoff（本地时间文本）比较语义零变化。
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
from backend.observability.trace_store import _MAX_ROWS, TraceStore, _serialize_trace
from backend.shared.logger import logger


class PostgresTraceStore(TraceStore):
    """trace_store 的 PostgreSQL 实现 — TraceStore 子类（isinstance 兼容）。

    db_path 参数保留但被忽略（签名兼容 SQLite 版调用方）；实际连接参数来自
    `OBS_DB_PG_CONFIG`（默认 agent_memory 库）。表名支持 `OBS_DB_PG_TABLE_PREFIX`
    前缀覆盖（测试隔离用）。
    """

    def __init__(self, db_path: str = "data/trace_store.db"):
        self._db_path = db_path  # 兼容保留，PG 模式下无意义
        self._lock = threading.Lock()
        self._table = os.getenv("OBS_DB_PG_TABLE_PREFIX", "") + "trace_store"
        self._init_db()

    # ---- 连接层（与 doc_registry_pg 同模式：每次操作独立连接，用完即关）----

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

    # ---- 建表（幂等，与 backend/sql/migrations/012_obs_trace_store_pg.sql 一致）----

    def _init_db(self):
        t = self._table
        with self._lock, self._conn() as conn:
            conn.cursor().execute(f"""
                CREATE TABLE IF NOT EXISTS {t} (
                    trace_id      TEXT PRIMARY KEY,
                    data          TEXT NOT NULL,
                    created_at    TEXT NOT NULL,
                    session_id    TEXT DEFAULT '',
                    status        TEXT DEFAULT '',
                    workflow_name TEXT DEFAULT '',
                    rejected      INTEGER DEFAULT 0,
                    duration_ms   INTEGER DEFAULT 0,
                    parent_id     TEXT
                )
            """)
            cur = conn.cursor()
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_created ON {t}(created_at DESC)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_session ON {t}(session_id)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_status ON {t}(status)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_rejected ON {t}(rejected)")

    # ---- 写入 ----

    def save(self, trace: Any):
        """持久化一条 trace。trace_id 重复时覆盖更新（语义同 SQLite 版）。"""
        try:
            data = _serialize_trace(trace)
            self.save_dict(data)
        except Exception as e:
            logger.warning(f"[TraceStore-PG] 持久化失败 {getattr(trace, 'id', '?')}: {e}")

    def save_dict(self, data: dict):
        try:
            trace_id = data.get("id", "")
            if not trace_id:
                return
            json_str = json.dumps(data, ensure_ascii=False, default=str)
            # 与 SQLite 版同源：本地时间文本（list_since cutoff 语义一致）
            now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            rejection = (data.get("metadata") or {}).get("rejection") or {}
            rejected_flag = 1 if (rejection.get("rejected")
                                  or data.get("status") == "rejected") else 0

            with self._lock, self._conn() as conn:
                self._exec(
                    conn,
                    f"""INSERT INTO {self._table}
                       (trace_id, data, created_at, session_id, status, workflow_name,
                        rejected, duration_ms, parent_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (trace_id) DO UPDATE SET
                         data = EXCLUDED.data, created_at = EXCLUDED.created_at,
                         session_id = EXCLUDED.session_id, status = EXCLUDED.status,
                         workflow_name = EXCLUDED.workflow_name,
                         rejected = EXCLUDED.rejected, duration_ms = EXCLUDED.duration_ms,
                         parent_id = EXCLUDED.parent_id""",
                    (trace_id, json_str, now,
                     data.get("session_id", ""), data.get("status", ""),
                     data.get("workflow_name", ""), rejected_flag,
                     int(data.get("duration_ms") or 0), data.get("parent_id")),
                )
                # 定期清理旧数据（语义同 SQLite 版 _MAX_ROWS）
                count = self._exec_scalar(
                    conn, f"SELECT COUNT(*) FROM {self._table}").fetchone()[0]
                if count > _MAX_ROWS:
                    conn.cursor().execute(
                        f"DELETE FROM {self._table} WHERE trace_id IN "
                        f"(SELECT trace_id FROM {self._table} "
                        f"ORDER BY created_at ASC LIMIT %s)",
                        (count - _MAX_ROWS + 100,),
                    )
        except Exception as e:
            logger.warning(f"[TraceStore-PG] save_dict 失败 {data.get('id', '?')}: {e}")

    # ---- 查询 ----

    def get(self, trace_id: str) -> dict | None:
        try:
            with self._lock, self._conn() as conn:
                row = self._exec(
                    conn,
                    f"SELECT data FROM {self._table} WHERE trace_id = %s",
                    (trace_id,),
                ).fetchone()
            if row:
                return json.loads(row["data"])
        except Exception as e:
            logger.warning(f"[TraceStore-PG] 读取失败 {trace_id}: {e}")
        return None

    def list(self, limit: int = 20) -> list[dict]:
        """最近 N 条 trace 摘要（不包含 spans 详情）。"""
        try:
            with self._lock, self._conn() as conn:
                rows = self._exec(
                    conn,
                    f"SELECT data FROM {self._table} ORDER BY created_at DESC LIMIT %s",
                    (limit,),
                ).fetchall()
            result = []
            for r in rows:
                try:
                    d = json.loads(r["data"])
                    d.pop("spans", None)
                    result.append(d)
                except Exception:
                    logger.debug("[TraceStore-PG] 行 JSON 解析失败（跳过脏数据）")
            return result
        except Exception as e:
            logger.warning(f"[TraceStore-PG] list 失败: {e}")
            return []

    def list_since(self, cutoff_iso: str, *, only_rejected: bool = False,
                   limit: int = 1000) -> list[dict]:
        """列出 created_at >= cutoff 的 trace（Evidence Gate 反向驱动用）。

        cutoff_iso 格式: "2026-08-03 12:00:00"（本地时间文本，与写入同构）。
        """
        try:
            sql = (f"SELECT data, created_at FROM {self._table} "
                   f"WHERE created_at >= %s ")
            if only_rejected:
                sql += "AND rejected = 1 "
            sql += f"ORDER BY created_at DESC LIMIT %s"
            with self._lock, self._conn() as conn:
                rows = self._exec(
                    conn, sql, (cutoff_iso, max(limit, 5000))).fetchall()
            result = []
            for r in rows:
                try:
                    d = json.loads(r["data"])
                except Exception:
                    continue
                if only_rejected:
                    # 兼容判定（与 SQLite 版一致）：列缺失时回退 JSON 判定
                    rej = (d.get("metadata") or {}).get("rejection") or {}
                    if not rej.get("rejected") and d.get("status") != "rejected":
                        continue
                d.pop("spans", None)
                result.append(d)
                if len(result) >= limit:
                    break
            return result
        except Exception as e:
            logger.warning(f"[TraceStore-PG] list_since 失败: {e}")
            return []
