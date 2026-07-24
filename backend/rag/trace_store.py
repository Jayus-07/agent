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

from backend.shared.logger import logger

# 默认路径（与 doc_registry 同级）
DEFAULT_TRACE_DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "trace_store.db"
)

_MAX_ROWS = 5000  # 最多保留条数


def _serialize_trace(trace: Any) -> dict:
    """TraceRecord → JSON 可序列化的 dict。"""
    if hasattr(trace, "__dict__"):
        d = {}
        for k, v in trace.__dict__.items():
            if k.startswith("_"):
                continue
            if isinstance(v, list):
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
        return sqlite3.connect(self._db_path)

    def _init_db(self):
        with self._lock, self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trace_store (
                    trace_id   TEXT PRIMARY KEY,
                    data       TEXT NOT NULL,      -- JSON
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ts_created ON trace_store(created_at DESC)")

    def save(self, trace: Any):
        """持久化一条 trace。trace_id 重复时跳过。"""
        try:
            data = _serialize_trace(trace)
            trace_id = data.get("id", "")
            if not trace_id:
                return
            json_str = json.dumps(data, ensure_ascii=False, default=str)
            now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

            with self._lock, self._conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO trace_store (trace_id, data, created_at) VALUES (?, ?, ?)",
                    (trace_id, json_str, now),
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
            logger.warning(f"[TraceStore] 持久化失败 {getattr(trace, 'id', '?')}: {e}")

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
                    pass
            return result
        except Exception as e:
            logger.warning(f"[TraceStore] list 失败: {e}")
            return []


# 模块级单例
_trace_store: TraceStore | None = None


def get_trace_store() -> TraceStore:
    global _trace_store
    if _trace_store is None:
        _trace_store = TraceStore()
    return _trace_store
