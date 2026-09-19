"""PostgresKeywordRuleStore — keyword_rules 的 PostgreSQL 连接层。

与 SQLite 版 `KeywordRuleStore` 对外接口完全一致（get_active/get_rules_by_doc_type/
get_keywords_for_doc_type/list_all/list_doc_types/list_categories/upsert/
batch_upsert/delete/toggle + 60s 缓存），仅替换连接层与 SQL 方言。引擎开关见
PG 为唯一实现（2026-09-17 SQLite 轨删除）。
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
from backend.config.database import BUSINESS_DB_CONFIG, RAG_STORES_PG_CONFIG
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
        # 版本治理迁移存在时，运行时只读 published 快照；未部署迁移的旧环境
        # 保留只读兼容，避免升级期间分类链路直接中断。
        try:
            snapshot = self.get_active_rule_snapshot()
            entries = snapshot.get("entries", [])
            by_doc_type: dict[str, list[str]] = {}
            by_doc_type_w: dict[str, list[tuple[str, int]]] = {}
            all_keywords: list[str] = []
            signal_rules: dict[str, list[str]] = {}
            for row in entries:
                kw = str(row["keyword"])
                dt = str(row["doc_type"])
                weight = int(row["weight"])
                by_doc_type.setdefault(dt, []).append(kw)
                by_doc_type_w.setdefault(dt, []).append((kw, weight))
                all_keywords.append(kw)
                if row.get("category"):
                    signal_rules.setdefault(str(row["category"]), []).append(kw)
            self._cache = {
                "keywords": all_keywords,
                "by_doc_type": by_doc_type,
                "by_doc_type_w": by_doc_type_w,
                "signal_rules": signal_rules,
                "rules_hash": snapshot.get("rules_hash", ""),
                "version": snapshot.get("version"),
            }
            self._cache_ts = time.time()
            return self._cache
        except LookupError:
            # 迁移已存在但尚无 published 版本：fail closed，不读未版本化表。
            self._cache = {
                "keywords": [], "by_doc_type": {}, "by_doc_type_w": {},
                "signal_rules": {}, "rules_hash": "", "version": None,
            }
            self._cache_ts = time.time()
            return self._cache
        except psycopg2.errors.UndefinedTable:
            logger.warning(
                "[KeywordStore-PG] 规则治理表尚未迁移，暂用 legacy keyword_rules 兼容读"
            )

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

    # ---- 版本化规则快照治理 ----

    @staticmethod
    def _snapshot_from_conn(conn: Any, version: int) -> dict[str, Any]:
        """在同一事务中读取版本与 entries，避免发布期间读到半份快照。"""
        version_cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        version_cur.execute(
            "SELECT * FROM ai.metadata_rule_versions WHERE version = %s",
            (version,),
        )
        row = version_cur.fetchone()
        if row is None:
            raise LookupError(f"metadata rule version not found: {version}")
        entry_cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        entry_cur.execute(
            """
            SELECT keyword, doc_type, category, weight, enabled
            FROM ai.metadata_rule_entries
            WHERE version = %s
            ORDER BY keyword
            """,
            (version,),
        )
        result = dict(row)
        result["entries"] = [dict(item) for item in entry_cur.fetchall()]
        return result

    def get_active_rule_snapshot(self) -> dict[str, Any]:
        with self._conn() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute(
                """
                SELECT * FROM ai.metadata_rule_versions
                WHERE status = 'published'
                ORDER BY effective_at DESC NULLS LAST, version DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            if row is None:
                raise LookupError("no published metadata rule snapshot")
            return self._snapshot_from_conn(conn, int(row["version"]))

    def get_rule_snapshot(self, version: int) -> dict[str, Any]:
        with self._conn() as conn:
            return self._snapshot_from_conn(conn, int(version))

    def list_rule_snapshots(self, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        with self._conn() as conn:
            cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cursor.execute(
                """
                SELECT version, status, taxonomy_version, rules_hash, actor,
                       reason, approval_id, approved_by, effective_at, created_at
                FROM ai.metadata_rule_versions
                ORDER BY version DESC
                LIMIT %s
                """,
                (safe_limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def invalidate_rule_cache(self) -> None:
        """发布/回滚后立即清空本进程的旧活动快照。"""
        self._cache = None
        self._cache_ts = 0

    def create_rule_snapshot(self, **payload) -> dict[str, Any]:
        entries = payload.pop("entries", [])
        with self._lock, self._conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO ai.metadata_rule_versions (
                    status, taxonomy_version, rules_hash, actor, reason
                ) VALUES (%s, %s, %s, %s, %s)
                RETURNING version
                """,
                (
                    payload.get("status", "draft"),
                    payload.get("taxonomy_version", ""),
                    payload.get("rules_hash", ""),
                    payload.get("actor", ""),
                    payload.get("reason", ""),
                ),
            )
            version = int(cursor.fetchone()[0])
            psycopg2.extras.execute_batch(
                conn.cursor(),
                """
                INSERT INTO ai.metadata_rule_entries
                    (version, keyword, doc_type, category, weight, enabled)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        version,
                        item["keyword"],
                        item["doc_type"],
                        item.get("category", ""),
                        int(item.get("weight", 1)),
                        int(item.get("enabled", 1)),
                    )
                    for item in entries
                ],
            )
            return self._snapshot_from_conn(conn, version)

    @staticmethod
    def _approval_in_conn(conn: Any, approval_id: str) -> tuple[bool, str]:
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT reviewer FROM ai.tool_approval_requests
                WHERE id = %s AND status = 'approved'
                """,
                (approval_id,),
            )
            row = cursor.fetchone()
        except Exception:
            return False, ""
        return (row is not None), (str(row[0] or "") if row else "")

    def is_rule_approval_approved(self, approval_id: str) -> str | None:
        # 审批单属于 agent_business；规则快照属于 agent_memory，不能在
        # RAG 库连接上查询，否则双库部署时会把“已审批”误判成不存在。
        conn = None
        try:
            conn = psycopg2.connect(**BUSINESS_DB_CONFIG, connect_timeout=3)
            approved, reviewer = self._approval_in_conn(conn, approval_id)
            return reviewer if approved else None
        except Exception as exc:
            logger.warning(f"[KeywordStore-PG] 审批状态读取失败，发布 fail closed: {exc}")
            return None
        finally:
            if conn is not None:
                conn.close()

    def publish_rule_snapshot(
        self, version: int, approval_id: str, actor: str
    ) -> dict[str, Any]:
        reviewer = self.is_rule_approval_approved(approval_id)
        if reviewer is None:
            raise PermissionError("approved approval_id is required")
        with self._lock, self._conn() as conn:
            # 进程内锁只覆盖单 worker； advisory lock 保证多 worker 发布时
            # 仍然只有一个 published 快照。
            conn.cursor().execute(
                "SELECT pg_advisory_xact_lock(hashtext('metadata_rule_publish'))"
            )
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status FROM ai.metadata_rule_versions WHERE version = %s FOR UPDATE",
                (int(version),),
            )
            row = cursor.fetchone()
            if row is None:
                raise LookupError(f"metadata rule version not found: {version}")
            if row[0] not in ("draft", "published"):
                raise ValueError(f"metadata rule version is not publishable: {version}")
            cursor.execute(
                """
                UPDATE ai.metadata_rule_versions
                SET status = 'rolled_back', updated_at = now()
                WHERE status = 'published' AND version <> %s
                """,
                (int(version),),
            )
            cursor.execute(
                """
                UPDATE ai.metadata_rule_versions
                SET status = 'published', approval_id = %s, approved_by = %s,
                    actor = %s, effective_at = now(), updated_at = now()
                WHERE version = %s
                """,
                (approval_id, reviewer, actor, int(version)),
            )
            return self._snapshot_from_conn(conn, int(version))

    def rollback_rule_snapshot(
        self, version: int, actor: str, reason: str = "manual rollback"
    ) -> dict[str, Any]:
        with self._lock, self._conn() as conn:
            conn.cursor().execute(
                "SELECT pg_advisory_xact_lock(hashtext('metadata_rule_publish'))"
            )
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status FROM ai.metadata_rule_versions WHERE version = %s FOR UPDATE",
                (int(version),),
            )
            row = cursor.fetchone()
            if row is None:
                raise LookupError(f"metadata rule version not found: {version}")
            if row[0] not in ("published", "rolled_back"):
                raise ValueError(f"metadata rule version is not rollbackable: {version}")
            cursor.execute(
                """
                UPDATE ai.metadata_rule_versions
                SET status = 'rolled_back', updated_at = now()
                WHERE status = 'published' AND version <> %s
                """,
                (int(version),),
            )
            cursor.execute(
                """
                UPDATE ai.metadata_rule_versions
                SET status = 'published', actor = %s, reason = %s,
                    effective_at = now(), updated_at = now()
                WHERE version = %s
                """,
                (actor, reason, int(version)),
            )
            return self._snapshot_from_conn(conn, int(version))

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
