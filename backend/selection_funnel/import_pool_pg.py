"""PostgresImportPoolStore — 导入候选池 PostgreSQL 连接层（2026-09-18 高并发设计）。

与 SQLite 版 `ImportPoolStore` 对外接口完全一致（isinstance 兼容）；
工厂按 SELECTION_FUNNEL_DB_BACKEND 分发（默认 postgres），SQLite 轨保留作
测试逃生舱（backend/tests/selection_funnel/conftest.py 统一注入隔离）。

库归属：agent_business（SELECTION_PG_CONFIG，业务族，对 NL2SQL 可见）。
schema 与 backend/sql/migrations/021_selection_funnel_pg.sql 保持一致。

高并发设计（用户拍板 2026-09-18「设计高并发的，以后都能用」）：
  1. ThreadedConnectionPool（min/max 读 config 层 DB_POOL_* 参数）——
     per-op 借还连接替代每操作新建 TCP+auth（PG 握手 1-5ms，高频写入下是首要瓶颈）
  2. 批量写入 psycopg2.extras.execute_values（一次网络往返写整批，替代逐条 INSERT）
  3. 表名前缀隔离（SELECTION_FUNNEL_PG_TABLE_PREFIX，测试用）
  4. 批次 id 带随机后缀（同秒多批不碰撞，与 SQLite 版同修）
语义纪律：list_candidates 保持哑管道（过滤查询，不去重）——同款去重统一由
build_pool 的 duplicate 规则负责（保留最新批次，旧行进 reasons 披露）。
方言映射（对齐 selection_decision/store_pg.py）：
  - `?` → `%s`；sqlite3 隐式事务 → psycopg2 显式 commit/rollback
  - 时间戳沿用应用侧生成的 ISO 文本（不依赖 PG 服务器时区，与全仓约定一致）
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

import psycopg2
import psycopg2.extras

from backend.config.database import DB_POOL_MAX_CONN, DB_POOL_MIN_CONN, SELECTION_PG_CONFIG
from backend.selection_funnel.import_pool import ImportPoolStore
from backend.shared.logger import logger

_PREFIX = os.getenv("SELECTION_FUNNEL_PG_TABLE_PREFIX", "")
_TABLE = f"{_PREFIX}import_candidates"

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    id BIGSERIAL PRIMARY KEY,
    batch_id TEXT NOT NULL,
    title TEXT NOT NULL,
    platform TEXT DEFAULT '',
    price DOUBLE PRECISION,
    original_price DOUBLE PRECISION,
    rating DOUBLE PRECISION,
    review_count INTEGER,
    sales INTEGER,
    unit_cost DOUBLE PRECISION,
    category TEXT DEFAULT '',
    url TEXT DEFAULT '',
    promo_text TEXT DEFAULT '',
    highlights TEXT DEFAULT '',
    extra_json TEXT DEFAULT '',
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sf_imp_category ON {_TABLE}(category);
CREATE INDEX IF NOT EXISTS idx_sf_imp_batch ON {_TABLE}(batch_id);
CREATE INDEX IF NOT EXISTS idx_sf_imp_url ON {_TABLE}(url);
"""

_INSERT_COLS = (
    "batch_id, title, platform, price, original_price, rating, review_count,"
    " sales, unit_cost, category, url, promo_text, highlights, extra_json, imported_at"
)

_pool: Any = None
_pool_lock = threading.Lock()


def _get_pool() -> Any:
    """进程级连接池单例（double-checked locking，min/max 对齐全仓 DB_POOL_*）。"""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                from psycopg2.pool import ThreadedConnectionPool
                _pool = ThreadedConnectionPool(
                    DB_POOL_MIN_CONN, DB_POOL_MAX_CONN, **SELECTION_PG_CONFIG)
                logger.info("[PostgresImportPoolStore] 连接池就绪（agent_business，"
                            "min=%d max=%d）", DB_POOL_MIN_CONN, DB_POOL_MAX_CONN)
    return _pool


@contextmanager
def pool_conn() -> Iterator[Any]:
    """从共享连接池借一连接：退出 commit（读也要）——SELECT 开启的事务若不收尾，
    连接带着 idle-in-transaction 归还池子，长期持锁阻塞 vacuum（2026-09-18 修）。
    本域两个 PG store 与后续同库新增 store 一律走这里，不各自裸借还。"""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


class PostgresImportPoolStore(ImportPoolStore):
    """导入候选池存储 — PostgreSQL 实现（isinstance 兼容，高并发连接池）。"""

    def __init__(self, db_path: str = ""):
        self._init_lock = threading.Lock()
        self._ensure_schema()
        logger.debug("[PostgresImportPoolStore] 初始化完成（agent_business）")

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        with pool_conn() as conn:
            yield conn

    def _ensure_schema(self) -> None:
        with self._init_lock, self._conn() as conn:
            conn.cursor().execute(_SCHEMA_SQL)

    def add_batch(self, rows: list[dict[str, Any]], category: str = "",
                  platform: str = "") -> tuple[str, int]:
        """写入一个导入批次（单事务 execute_values，失败整批回滚）。

        行内 category/platform 缺失时用批次级默认补齐；语义与 SQLite 版一致。
        Returns: (batch_id, 写入行数)
        """
        from datetime import datetime
        batch_id = f"imp-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        now = datetime.now().isoformat(timespec="seconds")
        values = [
            (batch_id,
             r.get("title") or "",
             r.get("platform") or platform,
             r.get("price"), r.get("original_price"), r.get("rating"),
             int(r["review_count"]) if r.get("review_count") is not None else None,
             int(r["sales"]) if r.get("sales") is not None else None,
             r.get("unit_cost"),
             r.get("category") or category,
             r.get("url") or "",
             r.get("promo_text") or "",
             r.get("highlights") or "",
             json.dumps(r.get("extra") or {}, ensure_ascii=False),
             now)
            for r in rows
        ]
        with self._conn() as conn:
            psycopg2.extras.execute_values(
                conn.cursor(),
                f"INSERT INTO {_TABLE} ({_INSERT_COLS}) VALUES %s",
                values)
        return batch_id, len(values)

    def list_candidates(self, category: str = "",
                        platform: str = "") -> list[dict[str, Any]]:
        """拉取候选（粗过滤，哑管道不去重，语义与 SQLite 版一致）。

        同款去重统一由 build_pool 的 duplicate 规则负责（保留最新批次）。
        返回字段与漏斗 _POOL_FIELDS 对齐 + sales/extra。
        """
        conds, params = [], []
        if category:
            conds.append("category LIKE %s")
            params.append(f"%{category}%")
        if platform:
            conds.append("platform = %s")
            params.append(platform)
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        sql = (f"SELECT * FROM {_TABLE} {where} ORDER BY id ASC")
        with self._conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["extra"] = _loads(d.pop("extra_json") or "{}")
            out.append(d)
        return out

    def clear_batch(self, batch_id: str) -> int:
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute(f"DELETE FROM {_TABLE} WHERE batch_id = %s", (batch_id,))
            return cur.rowcount

    def history_by_keys(self, keys: list[tuple[str, str, str]],
                        limit: int = 50) -> dict[str, list[dict[str, Any]]]:
        """批量取同款历史批次（趋势接线，2026-09-18）：一次 ANY 数组查询替代逐候选 N 次。

        Args:
            keys: [(url, title, platform), ...]（候选集的判定键原料，url 可空）
            limit: 每款最多返回快照条数（与竞品 store.history 的 limit 同义）
        Returns:
            {dedup_key(url,title,platform): [快照 新→旧]}。快照字段对齐
            scoring 的 history 口径：crawled_at=imported_at（该行的数据时点，
            供热度日增速计算）；in_stock 不补造（导入表无此列，评分层按中性处理）。
        """
        from backend.selection_funnel.import_pool import dedup_key
        wanted = {dedup_key(u, t, p) for u, t, p in keys}
        urls = sorted({(u or "").strip() for u, _, _ in keys if (u or "").strip()})
        titles = sorted({(t or "").strip() for _, t, _ in keys if (t or "").strip()})
        if not urls and not titles:
            return {}
        conds, params = [], []
        if urls:
            conds.append("(url <> '' AND url = ANY(%s))")
            params.append(urls)
        if titles:
            conds.append("title = ANY(%s)")
            params.append(titles)
        sql = f"SELECT * FROM {_TABLE} WHERE {' OR '.join(conds)} ORDER BY id DESC"
        with self._conn() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            d = dict(r)
            key = dedup_key(d.get("url") or "", d.get("title") or "",
                            d.get("platform") or "")
            if key not in wanted:
                continue   # title 命中但 (title, platform) 不同款 → 剔除
            snap = {"title": d.get("title"), "url": d.get("url"),
                    "platform": d.get("platform"), "price": d.get("price"),
                    "rating": d.get("rating"), "review_count": d.get("review_count"),
                    "sales": d.get("sales"), "imported_at": d.get("imported_at"),
                    "crawled_at": d.get("imported_at")}
            grouped.setdefault(key, []).append(snap)
        return {k: v[:limit] for k, v in grouped.items()}

    def count(self) -> int:
        with self._conn() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {_TABLE}")
            return cur.fetchone()[0]


def _loads(raw: str) -> dict:
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
