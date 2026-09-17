"""PostgresCompetitorStore — 竞品监控 4 表的 PostgreSQL 连接层（迁移计划 Batch D）。

与 SQLite 版 `CompetitorStore` 对外接口完全一致。
PG 为唯一实现（2026-09-17 SQLite 轨删除）。

库归属：agent_business（业务数据，对 NL2SQL 可见）。
schema 与 backend/sql/migrations/016_business_stores_pg.sql 保持一致。

方言映射：
  - `?`                     → `%s`
  - `INTEGER AUTOINCREMENT` → `BIGSERIAL`（INSERT ... RETURNING id）
  - `INSERT ... ON CONFLICT(url) DO UPDATE` 语法 PG 原生支持，直接沿用
  - sqlite3 隐式事务        → psycopg2 显式 commit/rollback（per-op 连接）
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator, Optional

import psycopg2
import psycopg2.extras

from backend.competitor.crypto import maybe_decrypt, maybe_encrypt
from backend.competitor.store import CompetitorStore
from backend.config.database import COMPETITOR_PG_CONFIG
from backend.shared.logger import logger

_PREFIX = os.getenv("COMPETITOR_PG_TABLE_PREFIX", "")
_T_WATCH = f"{_PREFIX}competitor_watchlist"
_T_SNAP = f"{_PREFIX}competitor_snapshots"
_T_CONFIG = f"{_PREFIX}competitor_config"
_T_EVENTS = f"{_PREFIX}competitor_events"

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {_T_WATCH} (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL UNIQUE,
    platform    TEXT DEFAULT 'generic',
    my_sku      TEXT DEFAULT '',
    frequency   TEXT DEFAULT 'daily',
    enabled     INTEGER DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS {_T_SNAP} (
    id             BIGSERIAL PRIMARY KEY,
    watchlist_id   BIGINT,
    url            TEXT NOT NULL,
    platform       TEXT DEFAULT 'generic',
    title          TEXT DEFAULT '',
    price          DOUBLE PRECISION,
    original_price DOUBLE PRECISION,
    currency       TEXT DEFAULT 'CNY',
    promo_text     TEXT DEFAULT '',
    rating         DOUBLE PRECISION,
    review_count   BIGINT,
    in_stock       INTEGER DEFAULT 1,
    highlights     TEXT DEFAULT '',
    extract_method TEXT DEFAULT 'llm',
    raw_excerpt    TEXT DEFAULT '',
    crawled_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_url_time
    ON {_T_SNAP}(url, crawled_at DESC);

CREATE TABLE IF NOT EXISTS {_T_CONFIG} (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS {_T_EVENTS} (
    id         BIGSERIAL PRIMARY KEY,
    platform   TEXT DEFAULT '',
    url        TEXT DEFAULT '',
    event_type TEXT NOT NULL,
    detail     TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_time ON {_T_EVENTS}(created_at DESC);
"""

_SNAPSHOT_COLS = (
    "watchlist_id", "url", "platform", "title", "price", "original_price",
    "currency", "promo_text", "rating", "review_count", "in_stock",
    "highlights", "extract_method", "raw_excerpt", "crawled_at",
)


class PostgresCompetitorStore(CompetitorStore):
    """竞品数据存储 — PostgreSQL 实现（isinstance 兼容）。"""

    def __init__(self, db_path: str = ""):
        self._db_path = db_path or "data/competitor.db"  # 兼容保留
        self._lock = threading.Lock()
        self._init_db()
        logger.info("[PostgresCompetitorStore] 初始化完成（agent_business）")

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        conn = psycopg2.connect(**COMPETITOR_PG_CONFIG)
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

    def _insert_returning_id(self, conn: Any, sql: str, params: tuple) -> int:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchone()[0]

    def _init_db(self):
        with self._lock, self._conn() as conn:
            conn.cursor().execute(_SCHEMA_SQL)

    # ── watchlist CRUD ──────────────────────────────

    def add_watch(self, name: str, url: str, platform: str = "generic",
                  my_sku: str = "", frequency: str = "daily") -> dict[str, Any]:
        """新增监控项（URL 已存在则更新名称等字段）"""
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._conn() as conn:
            self._exec(
                conn,
                f"""INSERT INTO {_T_WATCH} (name, url, platform, my_sku, frequency, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (url) DO UPDATE SET name = EXCLUDED.name,
                        platform = EXCLUDED.platform, my_sku = EXCLUDED.my_sku,
                        frequency = EXCLUDED.frequency""",
                (name, url, platform, my_sku, frequency, now),
            )
        row = self.get_watch_by_url(url)
        logger.info(f"[PostgresCompetitorStore] watchlist upsert: {name} ({url})")
        return row

    def list_watch(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        sql = f"SELECT * FROM {_T_WATCH}"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY id"
        with self._conn() as conn:
            return [dict(r) for r in self._exec(conn, sql).fetchall()]

    def get_watch_by_url(self, url: str) -> Optional[dict[str, Any]]:
        with self._conn() as conn:
            row = self._exec(
                conn, f"SELECT * FROM {_T_WATCH} WHERE url = %s", (url,)
            ).fetchone()
            return dict(row) if row else None

    def remove_watch(self, url: str) -> bool:
        """删除监控项（不删除快照历史）"""
        with self._lock, self._conn() as conn:
            cur = self._exec(
                conn, f"DELETE FROM {_T_WATCH} WHERE url = %s", (url,)
            )
            deleted = cur.rowcount > 0
        if deleted:
            logger.info(f"[PostgresCompetitorStore] watchlist removed: {url}")
        return deleted

    def toggle_watch(self, url: str, enabled: bool) -> Optional[dict[str, Any]]:
        """启用/停用监控项"""
        with self._lock, self._conn() as conn:
            self._exec(
                conn,
                f"UPDATE {_T_WATCH} SET enabled = %s WHERE url = %s",
                (1 if enabled else 0, url),
            )
        row = self.get_watch_by_url(url)
        if row:
            logger.info(
                f"[PostgresCompetitorStore] watchlist "
                f"{'enabled' if enabled else 'disabled'}: {url}"
            )
        return row

    # ── snapshots ───────────────────────────────────

    def save_snapshot(self, snap: dict[str, Any]) -> int:
        """保存一次抓取快照，返回快照 id"""
        snap = {**snap}
        snap.setdefault("crawled_at", datetime.now().isoformat(timespec="seconds"))
        snap.setdefault("extract_method", "llm")
        snap.setdefault("currency", "CNY")
        values = [snap.get(c) for c in _SNAPSHOT_COLS]
        with self._lock, self._conn() as conn:
            return self._insert_returning_id(
                conn,
                f"INSERT INTO {_T_SNAP} ({', '.join(_SNAPSHOT_COLS)}) "
                f"VALUES ({', '.join(['%s'] * len(_SNAPSHOT_COLS))}) RETURNING id",
                tuple(values),
            )

    def latest_snapshot(self, url: str, before_id: Optional[int] = None) -> Optional[dict[str, Any]]:
        """最近一次快照（before_id 用于取"上一次"，做变价对比）"""
        sql = f"SELECT * FROM {_T_SNAP} WHERE url = %s"
        params: list[Any] = [url]
        if before_id:
            sql += " AND id < %s"
            params.append(before_id)
        sql += " ORDER BY id DESC LIMIT 1"
        with self._conn() as conn:
            row = self._exec(conn, sql, tuple(params)).fetchone()
            return dict(row) if row else None

    def list_snapshots(self) -> list[dict]:
        """全部快照（按 crawled_at 升序，仅趋势聚合所需列）"""
        cols = ("id, url, platform, title, price, rating, review_count, "
                "highlights, in_stock, crawled_at")
        with self._conn() as conn:
            return [dict(r) for r in self._exec(
                conn, f"SELECT {cols} FROM {_T_SNAP} ORDER BY crawled_at"
            ).fetchall()]

    def all_snapshots_full(self) -> list[dict]:
        """全部快照全字段（排除 raw_excerpt 大字段）"""
        cols = ("id, url, platform, title, price, original_price, currency, promo_text, "
                "rating, review_count, in_stock, highlights, crawled_at")
        with self._conn() as conn:
            return [dict(r) for r in self._exec(
                conn, f"SELECT {cols} FROM {_T_SNAP} ORDER BY id"
            ).fetchall()]

    def history(self, url: str, limit: int = 10) -> list[dict[str, Any]]:
        """历史快照（新→旧），支撑价格趋势"""
        with self._conn() as conn:
            rows = self._exec(
                conn,
                f"SELECT * FROM {_T_SNAP} WHERE url = %s ORDER BY id DESC LIMIT %s",
                (url, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── config (key-value) ─────────────────────────

    def get_config(self, key: str) -> Optional[str]:
        """读取配置项（Cookie 等敏感值自动解密）"""
        with self._conn() as conn:
            row = self._exec(
                conn, f"SELECT value FROM {_T_CONFIG} WHERE key = %s", (key,)
            ).fetchone()
            if row is None:
                return None
            return maybe_decrypt(key, row["value"])

    def set_config(self, key: str, value: str) -> None:
        """写入配置项（UPSERT，Cookie 等敏感值自动加密）"""
        encrypted = maybe_encrypt(key, value)
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._conn() as conn:
            self._exec(
                conn,
                f"""INSERT INTO {_T_CONFIG} (key, value, updated_at)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value, updated_at = EXCLUDED.updated_at""",
                (key, encrypted, now),
            )
        logger.info(f"[PostgresCompetitorStore] config set: {key}")

    def delete_config(self, key: str) -> bool:
        """删除配置项"""
        with self._lock, self._conn() as conn:
            cur = self._exec(conn, f"DELETE FROM {_T_CONFIG} WHERE key = %s", (key,))
            return cur.rowcount > 0

    # ── 风控事件日志 ─────────────────────────────────

    def log_event(self, platform: str, url: str, event_type: str, detail: str = "") -> int:
        """记录一条风控/降级事件，返回事件 id"""
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._conn() as conn:
            return self._insert_returning_id(
                conn,
                f"INSERT INTO {_T_EVENTS} (platform, url, event_type, detail, created_at) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (platform, url, event_type, detail, now),
            )

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        """最近事件（新→旧）"""
        with self._conn() as conn:
            rows = self._exec(
                conn, f"SELECT * FROM {_T_EVENTS} ORDER BY id DESC LIMIT %s", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
