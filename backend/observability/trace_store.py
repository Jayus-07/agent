"""Trace 持久化存储 — SQLite。

TraceCollector 内存最多 200 条，重启即丢失。此模块在 trace 完成时将
TraceRecord 序列化为 JSON 写入 SQLite，查询时内存未命中则从此兜底。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict
from typing import Any

from backend.infra.sqlite import get_connection
from backend.shared.logger import logger

# 默认路径（与 doc_registry 同级）
DEFAULT_TRACE_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "trace_store.db"
)

_MAX_ROWS = 5000  # 最多保留条数

# P2-9: 高频过滤字段从 JSON blob 提升为独立列（服务端 SQL 过滤，
# 消除 list_since 在 Python 端解析最多 5000 行 JSON 的开销）。
_SUMMARY_COLUMNS = {
    "session_id": "TEXT",
    "status": "TEXT",
    "workflow_name": "TEXT",
    "rejected": "INTEGER",
    "duration_ms": "INTEGER",
    "parent_id": "TEXT",
}


def _serialize_trace(trace: Any) -> dict:
    """TraceRecord → JSON 可序列化的 dict。跳过 _ 前缀内部属性
    （如 Span._t0 计时器，非数据字段，序列化会带出噪音）。"""
    if hasattr(trace, "__dict__"):
        d = {}
        for k, v in trace.__dict__.items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict):
                d[k] = {dk: dv for dk, dv in v.items()
                        if not (isinstance(dk, str) and dk.startswith("_"))}
            elif isinstance(v, list):
                d[k] = [_serialize_trace(x) if hasattr(x, "__dict__") else x for x in v]
            elif hasattr(v, "__dict__"):
                d[k] = _serialize_trace(v)
            else:
                d[k] = v
        return d
    return trace


class TraceStore:
    """线程安全的 trace 持久化存储。"""

    def __init__(self, db_path: str = DEFAULT_TRACE_DB_PATH):
        self._db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self):
        return get_connection(self._db_path)

    def _init_db(self):
        with self._lock, self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trace_store (
                    trace_id   TEXT PRIMARY KEY,
                    data       TEXT NOT NULL,      -- JSON
                    created_at TEXT NOT NULL
                )
            """)
            # P2-9: 老表迁移 — 补齐摘要列（新建表直接 ALTER 也是幂等的）
            existing = {row[1] for row in conn.execute("PRAGMA table_info(trace_store)")}
            for col, col_type in _SUMMARY_COLUMNS.items():
                if col not in existing:
                    conn.execute(f"ALTER TABLE trace_store ADD COLUMN {col} {col_type}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ts_created ON trace_store(created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ts_session ON trace_store(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ts_status ON trace_store(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ts_rejected ON trace_store(rejected)")
            # 历史行回填 rejected（JSON 内 metadata.rejection.rejected；
            # 无 JSON1 扩展的 SQLite 静默跳过，新写入不受影响）
            try:
                conn.execute(
                    "UPDATE trace_store SET rejected = 1 "
                    "WHERE rejected IS NULL AND "
                    "json_extract(data, '$.metadata.rejection.rejected') = 1"
                )
                conn.execute(
                    "UPDATE trace_store SET rejected = 0 WHERE rejected IS NULL"
                )
            except sqlite3.Error:
                logger.debug("[TraceStore] 历史行 rejected 回填跳过（无 JSON1）", exc_info=True)

    def save(self, trace: Any):
        """持久化一条 trace。trace_id 重复时覆盖更新。"""
        try:
            data = _serialize_trace(trace)
            self.save_dict(data)
        except Exception as e:
            logger.warning(f"[TraceStore] 持久化失败 {getattr(trace, 'id', '?')}: {e}")

    def save_dict(self, data: dict):
        """持久化一条已序列化的 trace dict（供 trace_writer 异步路径调用）。"""
        try:
            trace_id = data.get("id", "")
            if not trace_id:
                return
            json_str = json.dumps(data, ensure_ascii=False, default=str)
            now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            # P2-9: 摘要列从序列化结果中提取，与 JSON 同源
            rejection = (data.get("metadata") or {}).get("rejection") or {}
            rejected_flag = 1 if (rejection.get("rejected")
                                  or data.get("status") == "rejected") else 0

            with self._lock, self._conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO trace_store "
                    "(trace_id, data, created_at, session_id, status, workflow_name,"
                    " rejected, duration_ms, parent_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (trace_id, json_str, now,
                     data.get("session_id", ""), data.get("status", ""),
                     data.get("workflow_name", ""), rejected_flag,
                     int(data.get("duration_ms") or 0), data.get("parent_id")),
                )
                # 定期清理旧数据
                count = conn.execute("SELECT COUNT(*) FROM trace_store").fetchone()[0]
                if count > _MAX_ROWS:
                    conn.execute(
                        "DELETE FROM trace_store WHERE trace_id IN "
                        "(SELECT trace_id FROM trace_store ORDER BY created_at ASC LIMIT ?)",
                        (count - _MAX_ROWS + 100,),
                    )
        except Exception as e:
            logger.warning(f"[TraceStore] save_dict 失败 {data.get('id', '?')}: {e}")

    def get(self, trace_id: str) -> dict | None:
        """从 SQLite 读取 trace 的 JSON dict。"""
        try:
            with self._lock, self._conn() as conn:
                row = conn.execute(
                    "SELECT data FROM trace_store WHERE trace_id = ?", (trace_id,)
                ).fetchone()
            if row:
                return json.loads(row[0])
        except Exception as e:
            logger.warning(f"[TraceStore] 读取失败 {trace_id}: {e}")
        return None

    def list(self, limit: int = 20) -> list[dict]:
        """最近 N 条 trace 摘要（不包含 spans 详情）。"""
        try:
            with self._lock, self._conn() as conn:
                rows = conn.execute(
                    "SELECT data FROM trace_store ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            result = []
            for (data_str,) in rows:
                try:
                    d = json.loads(data_str)
                    # 去掉 spans 减少传输量
                    d.pop("spans", None)
                    result.append(d)
                except Exception:
                    logger.debug("[P1-10] trace 行 JSON 解析失败（跳过脏数据）", exc_info=True)
            return result
        except Exception as e:
            logger.warning(f"[TraceStore] list 失败: {e}")
            return []

    def list_since(self, cutoff_iso: str, *, only_rejected: bool = False,
                   limit: int = 1000) -> list[dict]:
        """列出 created_at >= cutoff 的 trace (Evidence Gate 反向驱动用)。

        cutoff_iso 格式: "2026-08-03 12:00:00" (local time, 与 _save 写入格式一致)

        Args:
            only_rejected: True 时只返回 metadata.rejection.rejected==True 的 trace
        """
        try:
            with self._lock, self._conn() as conn:
                # 先按 created_at 粗筛，rejected 走索引列纯 SQL 过滤
                #（P2-9：旧实现在 Python 端解析最多 5000 行 JSON 再滤）
                sql = ("SELECT data, created_at FROM trace_store "
                       "WHERE created_at >= ? ")
                if only_rejected:
                    sql += "AND rejected = 1 "
                sql += "ORDER BY created_at DESC LIMIT ?"
                rows = conn.execute(sql, (cutoff_iso, max(limit, 5000))).fetchall()
            result = []
            for (data_str, _ts) in rows:
                try:
                    d = json.loads(data_str)
                except Exception:
                    continue
                if only_rejected:
                    # 兼容无 JSON1 回填的历史行：列缺失时回退 JSON 判定
                    rej = (d.get("metadata") or {}).get("rejection") or {}
                    if not rej.get("rejected") and d.get("status") != "rejected":
                        continue
                d.pop("spans", None)
                result.append(d)
                if len(result) >= limit:
                    break
            return result
        except Exception as e:
            logger.warning(f"[TraceStore] list_since 失败: {e}")
            return []


# 模块级单例
_trace_store: TraceStore | None = None


def get_trace_store() -> TraceStore:
    global _trace_store
    if _trace_store is None:
        _trace_store = TraceStore()
    return _trace_store
