"""SQLite 连接工厂 — WAL 模式 + thread-local 连接复用。

统一项目中所有 SQLite 数据库的连接管理：
  - WAL 模式：读写并发不互斥（读者不阻塞写者）
  - busy_timeout 5s：写-写冲突时短暂重试而非立即报错
  - thread-local：每个线程复用同一连接，避免 connect-per-op 开销

用法:
    from backend.infra.sqlite import get_connection
    conn = get_connection("/path/to/db.sqlite")
"""

from __future__ import annotations

import os
import sqlite3
import threading
from typing import Any

_local = threading.local()


def get_connection(db_path: str, *, row_factory: Any = None) -> sqlite3.Connection:
    """获取当前线程的 SQLite 连接（thread-local 复用）。

    首次调用时创建连接并启用 WAL + busy_timeout。
    后续同线程调用直接返回缓存连接。
    """
    conns: dict[str, sqlite3.Connection] = getattr(_local, "conns", None)
    if conns is None:
        conns = {}
        _local.conns = conns

    conn = conns.get(db_path)
    if conn is not None:
        try:
            conn.execute("SELECT 1")
            if row_factory is not None:
                conn.row_factory = row_factory
            return conn
        except sqlite3.Error:
            conn = None

    abs_path = os.path.abspath(db_path)
    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)

    conn = sqlite3.connect(abs_path, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    if row_factory is not None:
        conn.row_factory = row_factory

    conns[db_path] = conn
    return conn
