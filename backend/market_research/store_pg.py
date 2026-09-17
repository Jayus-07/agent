"""PostgresMarketResearchStore — 市场调研两表的 PostgreSQL 连接层（迁移计划 Batch D）。

与 SQLite 版 `MarketResearchStore` 对外接口完全一致。
PG 为唯一实现（2026-09-17 SQLite 轨删除）。

库归属：agent_business（业务数据，对 NL2SQL 可见）。
schema 与 backend/sql/migrations/016_business_stores_pg.sql 保持一致。

方言映射：
  - `?`                    → `%s`
  - `INSERT OR IGNORE`     → `INSERT ... ON CONFLICT (id) DO NOTHING`
  - `INSERT OR REPLACE`    → `INSERT ... ON CONFLICT (task_id, evidence_id) DO UPDATE`
  - `rowid`（tie-breaker） → `ctid`
  - sqlite3 隐式事务       → psycopg2 显式 commit/rollback（per-op 连接）
"""

from __future__ import annotations

import json as _json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

import psycopg2
import psycopg2.extras

from backend.config.database import MARKET_RESEARCH_PG_CONFIG
from backend.market_research.store import MarketResearchStore
from backend.shared.logger import logger

_PREFIX = os.getenv("MARKET_RESEARCH_PG_TABLE_PREFIX", "")
_T_TASKS = f"{_PREFIX}mr_tasks"
_T_EVIDENCE = f"{_PREFIX}mr_evidence"

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {_T_TASKS} (
    id           TEXT PRIMARY KEY,
    inputs_json  TEXT NOT NULL,
    status       TEXT NOT NULL,
    report_md    TEXT,
    trace_id     TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_mr_tasks_created ON {_T_TASKS}(created_at DESC);

CREATE TABLE IF NOT EXISTS {_T_EVIDENCE} (
    task_id       TEXT NOT NULL,
    evidence_id   TEXT NOT NULL,
    title         TEXT,
    url           TEXT,
    source_type   TEXT,
    raw_text      TEXT,
    numbers_json  TEXT,
    search_query  TEXT,
    fetched_at    TEXT,
    published_at  TEXT,
    PRIMARY KEY (task_id, evidence_id)
);
"""


class PostgresMarketResearchStore(MarketResearchStore):
    """市场调研任务 + 证据存储 — PostgreSQL 实现（isinstance 兼容）。"""

    def __init__(self, db_path: str = "data/market_research.db"):
        self._db_path = db_path  # 兼容保留
        self._lock = threading.Lock()
        self._init_db()
        logger.debug("[PostgresMarketResearchStore] 初始化完成（agent_business）")

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        conn = psycopg2.connect(**MARKET_RESEARCH_PG_CONFIG)
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

    def _init_db(self):
        with self._lock, self._conn() as conn:
            conn.cursor().execute(_SCHEMA_SQL)

    # ── 任务 ────────────────────────────────────

    def create(self, inputs: dict[str, Any]) -> str:
        task_id = uuid.uuid4().hex[:12]
        self.ensure_task(task_id, inputs)
        return task_id

    def ensure_task(self, task_id: str, inputs: dict[str, Any] | None = None) -> None:
        with self._lock, self._conn() as conn:
            self._exec(
                conn,
                f"""INSERT INTO {_T_TASKS}
                   (id, inputs_json, status, created_at) VALUES (%s, %s, 'running', %s)
                   ON CONFLICT (id) DO NOTHING""",
                (
                    task_id,
                    _json.dumps(inputs or {}, ensure_ascii=False, default=str),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

    def update_result(self, task_id: str, *, status: str, report_md: str = "",
                      trace_id: str = "", error: str = "") -> None:
        with self._lock, self._conn() as conn:
            self._exec(
                conn,
                f"""UPDATE {_T_TASKS}
                   SET status = %s, report_md = %s, trace_id = %s, error = %s, finished_at = %s
                   WHERE id = %s""",
                (
                    status, report_md, trace_id, error,
                    datetime.now().isoformat(timespec="seconds"), task_id,
                ),
            )

    def list(self, page: int = 1, page_size: int = 20) -> list[dict[str, Any]]:
        offset = (page - 1) * page_size
        with self._conn() as conn:
            rows = self._exec(
                conn,
                f"""SELECT id, status, trace_id, error, created_at, finished_at, inputs_json
                   FROM {_T_TASKS} ORDER BY created_at DESC, ctid DESC LIMIT %s OFFSET %s""",
                (page_size, offset),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["inputs"] = _json.loads(d.pop("inputs_json"))
            except (TypeError, ValueError):
                d["inputs"] = {}
            out.append(d)
        return out

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = self._exec(
                conn, f"SELECT * FROM {_T_TASKS} WHERE id = %s", (task_id,)
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        try:
            d["inputs"] = _json.loads(d.pop("inputs_json"))
        except (TypeError, ValueError):
            d["inputs"] = {}
        return d

    # ── 证据 ────────────────────────────────────

    def add_evidence(self, task_id: str, evidence: list[dict[str, Any]]) -> int:
        """批量写入标准化证据行，返回写入条数。"""
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._conn() as conn:
            for e in evidence:
                self._exec(
                    conn,
                    f"""INSERT INTO {_T_EVIDENCE}
                       (task_id, evidence_id, title, url, source_type, raw_text,
                        numbers_json, search_query, fetched_at, published_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (task_id, evidence_id) DO UPDATE SET
                            title = EXCLUDED.title,
                            url = EXCLUDED.url,
                            source_type = EXCLUDED.source_type,
                            raw_text = EXCLUDED.raw_text,
                            numbers_json = EXCLUDED.numbers_json,
                            search_query = EXCLUDED.search_query,
                            fetched_at = EXCLUDED.fetched_at,
                            published_at = EXCLUDED.published_at""",
                    (
                        task_id, e["evidence_id"], e.get("title", ""), e.get("url", ""),
                        e.get("source_type", "media"), e.get("raw_text", ""),
                        _json.dumps(e.get("numbers", []), ensure_ascii=False),
                        e.get("search_query", ""), e.get("fetched_at", now),
                        e.get("published_at"),
                    ),
                )
        return len(evidence)

    def list_evidence(self, task_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = self._exec(
                conn,
                f"SELECT * FROM {_T_EVIDENCE} WHERE task_id = %s ORDER BY evidence_id",
                (task_id,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["numbers"] = _json.loads(d.pop("numbers_json") or "[]")
            except (TypeError, ValueError):
                d["numbers"] = []
            out.append(d)
        return out
