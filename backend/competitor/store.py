"""competitor/store.py — 竞品监控存储（SQLite）

两表设计:
  - competitor_watchlist  — 用户配置的监控项（竞品 URL / 名称 / 平台）
  - competitor_snapshots  — 每次抓取的结构化快照（支撑价格趋势 / 变价告警）

设计要点:
  - 快照 append-only：竞品网站改版后历史数据不丢，可回溯
  - 原始正文存档（raw_excerpt），抽取规则出错时可溯源排查
"""
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any, Optional

from backend.competitor.crypto import maybe_decrypt, maybe_encrypt
from backend.shared.logger import logger

# 项目根目录下的 data/（与其他模块 data/ 目录约定一致）
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)
COMPETITOR_DB_PATH = os.getenv(
    "COMPETITOR_DB_PATH", os.path.join(_PROJECT_ROOT, "data", "competitor.db")
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS competitor_watchlist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,            -- 竞品/商品名（用户可读）
    url         TEXT NOT NULL UNIQUE,     -- 监控页面地址
    platform    TEXT DEFAULT 'generic',   -- jd / taobao / amazon / official / generic
    my_sku      TEXT DEFAULT '',          -- 对应的自家商品 SKU（可选，用于对比）
    frequency   TEXT DEFAULT 'daily',     -- daily / 4h / weekly
    enabled     INTEGER DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS competitor_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    watchlist_id   INTEGER,               -- 关联 watchlist.id（直接分析未入 watchlist 的 URL 时为 NULL）
    url            TEXT NOT NULL,
    platform       TEXT DEFAULT 'generic',
    title          TEXT DEFAULT '',
    price          REAL,                  -- 现价
    original_price REAL,                  -- 原价/划线价
    currency       TEXT DEFAULT 'CNY',
    promo_text     TEXT DEFAULT '',       -- 促销活动文案
    rating         REAL,                  -- 评分
    review_count   INTEGER,               -- 评价数
    in_stock       INTEGER DEFAULT 1,     -- 1=有货 0=无货
    highlights     TEXT DEFAULT '',       -- 卖点摘要（逗号分隔）
    extract_method TEXT DEFAULT 'llm',    -- llm / regex
    raw_excerpt    TEXT DEFAULT '',       -- 原始正文存档（截断）
    crawled_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_url_time
    ON competitor_snapshots(url, crawled_at DESC);

CREATE TABLE IF NOT EXISTS competitor_config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS competitor_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    platform   TEXT DEFAULT '',           -- 平台标识
    url        TEXT DEFAULT '',           -- 触发 URL
    event_type TEXT NOT NULL,             -- blocked / login_redirect / cooldown / halt / robots_skip / degrade
    detail     TEXT DEFAULT '',           -- 人类可读描述
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_time ON competitor_events(created_at DESC);
"""


class CompetitorStore:
    """线程安全的竞品数据存储"""

    def __init__(self, db_path: str = COMPETITOR_DB_PATH):
        self._db_path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
        logger.info(f"[CompetitorStore] 初始化: {db_path}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── watchlist CRUD ──────────────────────────────

    def add_watch(self, name: str, url: str, platform: str = "generic",
                  my_sku: str = "", frequency: str = "daily") -> dict[str, Any]:
        """新增监控项（URL 已存在则更新名称等字段）"""
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO competitor_watchlist (name, url, platform, my_sku, frequency, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(url) DO UPDATE SET name=excluded.name,
                        platform=excluded.platform, my_sku=excluded.my_sku,
                        frequency=excluded.frequency""",
                (name, url, platform, my_sku, frequency, now),
            )
        row = self.get_watch_by_url(url)
        logger.info(f"[CompetitorStore] watchlist upsert: {name} ({url})")
        return row

    def list_watch(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM competitor_watchlist"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY id"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql).fetchall()]

    def get_watch_by_url(self, url: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM competitor_watchlist WHERE url = ?", (url,)
            ).fetchone()
            return dict(row) if row else None

    def remove_watch(self, url: str) -> bool:
        """删除监控项（不删除快照历史，仅移除监控配置）"""
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM competitor_watchlist WHERE url = ?", (url,)
            )
            deleted = cur.rowcount > 0
        if deleted:
            logger.info(f"[CompetitorStore] watchlist removed: {url}")
        return deleted

    def toggle_watch(self, url: str, enabled: bool) -> Optional[dict[str, Any]]:
        """启用/停用监控项"""
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE competitor_watchlist SET enabled = ? WHERE url = ?",
                (1 if enabled else 0, url),
            )
        row = self.get_watch_by_url(url)
        if row:
            logger.info(f"[CompetitorStore] watchlist {'enabled' if enabled else 'disabled'}: {url}")
        return row

    # ── snapshots ───────────────────────────────────

    def save_snapshot(self, snap: dict[str, Any]) -> int:
        """保存一次抓取快照，返回快照 id"""
        cols = ("watchlist_id", "url", "platform", "title", "price", "original_price",
                "currency", "promo_text", "rating", "review_count", "in_stock",
                "highlights", "extract_method", "raw_excerpt", "crawled_at")
        snap = {**snap}
        snap.setdefault("crawled_at", datetime.now().isoformat(timespec="seconds"))
        snap.setdefault("extract_method", "llm")
        snap.setdefault("currency", "CNY")
        values = [snap.get(c) for c in cols]
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                f"INSERT INTO competitor_snapshots ({', '.join(cols)}) "
                f"VALUES ({', '.join('?' * len(cols))})",
                values,
            )
            return cur.lastrowid

    def latest_snapshot(self, url: str, before_id: Optional[int] = None) -> Optional[dict[str, Any]]:
        """最近一次快照（before_id 用于取"上一次"，做变价对比）"""
        sql = "SELECT * FROM competitor_snapshots WHERE url = ?"
        params: list[Any] = [url]
        if before_id:
            sql += " AND id < ?"
            params.append(before_id)
        sql += " ORDER BY id DESC LIMIT 1"
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def list_snapshots(self) -> list[dict]:
        """全部快照（按 crawled_at 升序，仅趋势聚合所需列，不含 raw_excerpt 大字段）"""
        cols = ("id, url, platform, title, price, rating, review_count, "
                "highlights, in_stock, crawled_at")
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(
                f"SELECT {cols} FROM competitor_snapshots ORDER BY crawled_at").fetchall()]

    def history(self, url: str, limit: int = 10) -> list[dict[str, Any]]:
        """历史快照（新→旧），支撑价格趋势"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM competitor_snapshots WHERE url = ? "
                "ORDER BY id DESC LIMIT ?",
                (url, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── config (key-value) ─────────────────────────

    def get_config(self, key: str) -> Optional[str]:
        """读取配置项（Cookie 等敏感值自动解密）"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM competitor_config WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            return maybe_decrypt(key, row["value"])

    def set_config(self, key: str, value: str) -> None:
        """写入配置项（UPSERT，Cookie 等敏感值自动加密）"""
        encrypted = maybe_encrypt(key, value)
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO competitor_config (key, value, updated_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, encrypted, now),
            )
        logger.info(f"[CompetitorStore] config set: {key}")

    def delete_config(self, key: str) -> bool:
        """删除配置项"""
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM competitor_config WHERE key = ?", (key,)
            )
            return cur.rowcount > 0

    # ── 风控事件日志（防封策略观测/降级依据） ─────────────

    def log_event(self, platform: str, url: str, event_type: str, detail: str = "") -> int:
        """记录一条风控/降级事件，返回事件 id"""
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO competitor_events (platform, url, event_type, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (platform, url, event_type, detail, now),
            )
            return cur.lastrowid

    def recent_events(self, limit: int = 50) -> list[dict[str, Any]]:
        """最近事件（新→旧），供观测端点/降级判断"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM competitor_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]


_store: Optional[CompetitorStore] = None


def get_store() -> CompetitorStore:
    """全局单例"""
    global _store
    if _store is None:
        _store = CompetitorStore()
    return _store


def reset_store() -> None:
    """重置全局单例（测试隔离 / 重新初始化时使用）"""
    global _store
    _store = None
