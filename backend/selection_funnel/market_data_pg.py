"""PostgresMarketStore — 赛道数据（关键词榜 + 差评）PostgreSQL 连接层（2026-09-18）。

生产唯一后端（2026-09-18 SQLite 轨退场；单测经 conftest 内存替身注入，
PG 真实行为由 test_import_pool_pg.py 锁定）。

库归属：agent_business（SELECTION_PG_CONFIG，与导入池同库同连接池）。
schema 与 backend/sql/migrations/021_selection_funnel_pg.sql 保持一致。

高并发设计（对齐 import_pool_pg.py）：
  - 共享进程级 ThreadedConnectionPool（同库不建第二池）
  - add_keywords / add_reviews 批量 execute_values 单事务写入
  - 表名前缀隔离（SELECTION_FUNNEL_PG_TABLE_PREFIX，测试用）
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime
from typing import Any

import psycopg2.extras

from backend.selection_funnel.import_pool_pg import pool_conn
from backend.shared.logger import logger

_PREFIX = os.getenv("SELECTION_FUNNEL_PG_TABLE_PREFIX", "")
_T_KW = f"{_PREFIX}keyword_stats"
_T_RV = f"{_PREFIX}product_reviews"

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {_T_KW} (
    id BIGSERIAL PRIMARY KEY,
    batch_id TEXT NOT NULL,
    category TEXT DEFAULT '',
    keyword TEXT,
    search_pop DOUBLE PRECISION,
    click_rate DOUBLE PRECISION,
    pay_rate DOUBLE PRECISION,
    competition DOUBLE PRECISION,
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sf_kw_cat ON {_T_KW}(category);
CREATE INDEX IF NOT EXISTS idx_sf_kw_pop ON {_T_KW}(search_pop DESC);
CREATE TABLE IF NOT EXISTS {_T_RV} (
    id BIGSERIAL PRIMARY KEY,
    batch_id TEXT NOT NULL,
    category TEXT DEFAULT '',
    product_title TEXT,
    content TEXT,
    star DOUBLE PRECISION,
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sf_rv_cat ON {_T_RV}(category);
"""

_kw_init_lock = threading.Lock()
_schema_ready = False


def _ensure_schema() -> None:
    """两表 schema 幂等初始化（进程内一次；IF NOT EXISTS 兜底并发首建）。"""
    global _schema_ready
    if _schema_ready:
        return
    with _kw_init_lock:
        if _schema_ready:
            return
        with pool_conn() as conn:
            conn.cursor().execute(_SCHEMA_SQL)
        _schema_ready = True
        logger.debug("[PostgresMarketStore] schema 就绪（agent_business）")


class PostgresMarketStore:
    """赛道数据存储 — PostgreSQL（生产唯一后端，高并发连接池）。

    借还连接统一走 import_pool_pg.pool_conn（读也 commit，杜绝
    idle-in-transaction 连接回流池子——2026-09-18 修）。
    """

    def __init__(self, db_path: str = ""):
        _ensure_schema()

    @staticmethod
    def _fetch(sql: str, params: tuple = ()) -> list[dict]:
        with pool_conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def add_keywords(self, rows: list[dict], category: str) -> tuple[str, int]:
        batch_id = f"kw-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        now = datetime.now().isoformat(timespec="seconds")
        with pool_conn() as conn:
            psycopg2.extras.execute_values(
                conn.cursor(),
                f"INSERT INTO {_T_KW} (batch_id, category, keyword, search_pop,"
                f" click_rate, pay_rate, competition, imported_at) VALUES %s",
                [(batch_id, category, r.get("keyword") or "", r.get("search_pop"),
                  r.get("click_rate"), r.get("pay_rate"), r.get("competition"), now)
                 for r in rows])
        return batch_id, len(rows)

    def add_reviews(self, rows: list[dict], category: str) -> tuple[str, int]:
        batch_id = f"rv-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        now = datetime.now().isoformat(timespec="seconds")
        with pool_conn() as conn:
            psycopg2.extras.execute_values(
                conn.cursor(),
                f"INSERT INTO {_T_RV} (batch_id, category, product_title,"
                f" content, star, imported_at) VALUES %s",
                [(batch_id, category, r.get("product_title") or "",
                  r.get("content") or "", r.get("star"), now) for r in rows])
        return batch_id, len(rows)

    def keywords(self, category: str = "") -> list[dict]:
        sql = f"SELECT keyword, search_pop, click_rate, pay_rate, competition FROM {_T_KW}"
        params: tuple = ()
        if category:
            sql += " WHERE category LIKE %s"
            params = (f"%{category}%",)
        sql += " ORDER BY search_pop DESC"
        return self._fetch(sql, params)

    def reviews(self, category: str = "") -> list[dict]:
        sql = f"SELECT product_title, content, star FROM {_T_RV}"
        params: tuple = ()
        if category:
            sql += " WHERE category LIKE %s"
            params = (f"%{category}%",)
        return self._fetch(sql, params)

    def clear_batch(self, batch_id: str) -> int:
        """按批次清除关键词/差评（batch_id 前缀 kw-/rv- 两表通吃，语义同 SQLite 版）。"""
        removed = 0
        with pool_conn() as conn:
            for table in (_T_KW, _T_RV):
                cur = conn.cursor()
                cur.execute(f"DELETE FROM {table} WHERE batch_id = %s", (batch_id,))
                removed += cur.rowcount
        return removed
