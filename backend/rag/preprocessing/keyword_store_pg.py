"""PostgresKeywordRuleStore — keyword_rules 的 PostgreSQL 连接层。

与 SQLite 版 `KeywordRuleStore` 对外接口完全一致（get_active/get_rules_by_doc_type/
get_keywords_for_doc_type/list_all/list_doc_types/list_categories/upsert/
batch_upsert/delete/toggle + 60s 缓存），仅替换连接层与 SQL 方言。引擎开关见
`backend/config/database.py::KEYWORD_STORE_BACKEND`（env `KEYWORD_STORE_BACKEND=postgres`）。
工厂分发见 `keyword_store.py::get_keyword_store`。

方言映射要点：
  - `INTEGER PRIMARY KEY AUTOINCREMENT` → `BIGSERIAL PRIMARY KEY`
  - `datetime('now')` → 应用侧生成 UTC 文本
  - `LIKE`（关键词搜索）→ `ILIKE`（SQLite LIKE 对 ASCII 不区分大小写）
  - 种子导入：`INSERT OR IGNORE`（无 UNIQUE 约束 = 纯 INSERT）→ 语义一致，
    去重由 Python 端 seen 集完成（与 SQLite 版相同）
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg2
import psycopg2.extras

from backend.config import DEFAULT_KEYWORDS, SIGNAL_RULES
from backend.config.database import RAG_STORES_PG_CONFIG
from backend.rag.preprocessing.keyword_store import KeywordRuleStore
from backend.shared.logger import logger


def _now_utc() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


class PostgresKeywordRuleStore(KeywordRuleStore):
    """keyword_rules 的 PostgreSQL 实现（KeywordRuleStore 子类，isinstance 兼容）。"""

    def __init__(self, db_path: str = "data/keyword_rules.db"):
        self._db_path = db_path  # 兼容保留，PG 模式下无意义
        self._lock = threading.Lock()
        self._table = os.getenv("RAG_STORES_PG_TABLE_PREFIX", "") + "keyword_rules"
        self._cache: dict[str, Any] | None = None
        self._cache_ts: float = 0
        self._init_db()

    # ---- 连接层 ----

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        conn = psycopg2.connect(**RAG_STORES_PG_CONFIG)
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

    # ---- 建表 + 种子（幂等，与 backend/sql/migrations/014_rag_stores_pg.sql 一致）----

    def _init_db(self) -> None:
        t = self._table
        with self._lock, self._conn() as conn:
            conn.cursor().execute(f"""
                CREATE TABLE IF NOT EXISTS {t} (
                    id         BIGSERIAL PRIMARY KEY,
                    keyword    TEXT NOT NULL,
                    doc_type   TEXT NOT NULL DEFAULT 'general',
                    category   TEXT NOT NULL DEFAULT '',
                    weight     INTEGER NOT NULL DEFAULT 1,
                    enabled    INTEGER NOT NULL DEFAULT 1,
                    source     TEXT NOT NULL DEFAULT 'seed',
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                )
            """)
            cur = conn.cursor()
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_enabled ON {t}(enabled)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_doc_type ON {t}(doc_type)")
            cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_category ON {t}(category)")
            count = self._exec_scalar(
                conn, f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            if count == 0:
                self._seed(conn)

    def _seed(self, conn: Any) -> None:
        """从 config 导入初始种子数据（与 SQLite 版同源：DEFAULT_KEYWORDS/SIGNAL_RULES
        + _SEED_DOC_TYPE_MAP 分配 doc_type）。"""
        kw_to_doc: dict[str, str] = {}
        for doc_type, kws in self._SEED_DOC_TYPE_MAP.items():
            for kw in kws:
                kw_lower = kw.lower()
                if kw_lower not in kw_to_doc:
                    kw_to_doc[kw_lower] = doc_type

        rows: list[tuple] = []
        seen: set[str] = set()
        now = _now_utc()
        for kw in DEFAULT_KEYWORDS:
            w = kw.strip()
            if w.lower() in seen:
                continue
            seen.add(w.lower())
            dt = kw_to_doc.get(w.lower(), "general")
            rows.append((w, dt, "", 1, 1, "seed", now, now))
        for cat, kws in SIGNAL_RULES.items():
            for kw in kws:
                w = kw.strip()
                if w.lower() in seen:
                    continue
                seen.add(w.lower())
                dt = kw_to_doc.get(w.lower(), "general")
                rows.append((w, dt, cat, 2, 1, "seed", now, now))
        psycopg2.extras.execute_batch(
            conn.cursor(),
            f"""INSERT INTO {self._table}
               (keyword, doc_type, category, weight, enabled, source, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            rows,
        )
        logger.info(f"[KeywordStore-PG] 种子数据导入: {len(rows)} 条")

    # ---- 查询（带缓存）----

    def _refresh_cache(self) -> dict:
        with self._conn() as conn:
            rows = self._exec(
                conn,
                f"""SELECT keyword, doc_type, category, weight FROM {self._table}
                    WHERE enabled=1 ORDER BY weight DESC""",
            ).fetchall()

        by_doc_type: dict[str, list[str]] = {}
        by_doc_type_w: dict[str, list[tuple[str, int]]] = {}
        all_keywords: list[str] = []
        signal_rules: dict[str, list[str]] = {}
        for r in rows:
            kw = r["keyword"]
            dt = r["doc_type"]
            w = r["weight"]
            if dt not in by_doc_type:
                by_doc_type[dt] = []
                by_doc_type_w[dt] = []
            by_doc_type[dt].append(kw)
            by_doc_type_w[dt].append((kw, w))
            all_keywords.append(kw)
            cat = r["category"]
            if cat:
                signal_rules.setdefault(cat, []).append(kw)

        self._cache = {
            "keywords": all_keywords,
            "by_doc_type": by_doc_type,
            "by_doc_type_w": by_doc_type_w,
            "signal_rules": signal_rules,
        }
        self._cache_ts = time.time()
        return self._cache

    # get_rules_by_doc_type / get_active / get_keywords_for_doc_type 继承（读缓存结构）


    # ---- CRUD ----

    def list_all(self, doc_type: str = "", category: str = "", enabled: int | None = None, search: str = "") -> list[dict]:
        conditions = []
        params: list[Any] = []
        if doc_type:
            conditions.append("doc_type = %s")
            params.append(doc_type)
        if category:
            conditions.append("category = %s")
            params.append(category)
        if enabled is not None:
            conditions.append("enabled = %s")
            params.append(enabled)
        if search:
            conditions.append("keyword ILIKE %s")
            params.append(f"%{search}%")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        with self._conn() as conn:
            rows = self._exec(
                conn,
                f"SELECT * FROM {self._table} {where} ORDER BY doc_type, weight DESC, keyword",
                tuple(params),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_doc_types(self) -> list[str]:
        with self._conn() as conn:
            rows = self._exec(
                conn,
                f"SELECT DISTINCT doc_type FROM {self._table} ORDER BY doc_type",
            ).fetchall()
        return [r["doc_type"] for r in rows]

    def list_categories(self) -> list[str]:
        with self._conn() as conn:
            rows = self._exec(
                conn,
                f"""SELECT DISTINCT category FROM {self._table}
                    WHERE category != '' ORDER BY category""",
            ).fetchall()
        return [r["category"] for r in rows]

    def upsert(self, keyword: str, doc_type: str = "general", category: str = "", weight: int = 1, enabled: int = 1) -> dict:
        with self._lock, self._conn() as conn:
            existing = self._exec(
                conn,
                f"SELECT id FROM {self._table} WHERE keyword = %s",
                (keyword,),
            ).fetchone()
            now = _now_utc()
            if existing:
                self._exec(
                    conn,
                    f"""UPDATE {self._table}
                       SET doc_type=%s, category=%s, weight=%s, enabled=%s, updated_at=%s
                       WHERE id=%s""",
                    (doc_type, category, weight, enabled, now, existing["id"]),
                )
            else:
                self._exec(
                    conn,
                    f"""INSERT INTO {self._table}
                       (keyword, doc_type, category, weight, enabled, source, updated_at)
                       VALUES (%s, %s, %s, %s, %s, 'manual', %s)""",
                    (keyword, doc_type, category, weight, enabled, now),
                )
        self._cache = None  # 失效缓存
        return {"ok": True, "keyword": keyword}

    def batch_upsert(self, items: list[dict]) -> dict:
        """批量导入（逐条 upsert，语义同 SQLite 版）。"""
        for item in items:
            kw = item.get("keyword", "").strip()
            if not kw:
                continue
            self.upsert(kw, item.get("doc_type", "general"), item.get("category", ""),
                        item.get("weight", 1), item.get("enabled", 1))
        return {"ok": True, "added": len(items)}

    def delete(self, keyword: str) -> dict:
        with self._lock, self._conn() as conn:
            self._exec(
                conn, f"DELETE FROM {self._table} WHERE keyword = %s", (keyword,))
        self._cache = None
        return {"ok": True}

    def toggle(self, keyword: str, enabled: int) -> dict:
        with self._lock, self._conn() as conn:
            self._exec(
                conn,
                f"UPDATE {self._table} SET enabled=%s, updated_at=%s WHERE keyword=%s",
                (enabled, _now_utc(), keyword),
            )
        self._cache = None
        return {"ok": True, "enabled": bool(enabled)}
