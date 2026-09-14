"""market_research/store.py — 市场调研任务与证据持久化

表结构对齐 selection_decision/store.py 惯例：SQLite + threading.Lock。
- mr_tasks：调研任务（列表接口不返回 report_md 大字段）
- mr_evidence：标准化证据行（task_id + evidence_id 联合主键，支持同一证据跨任务复用）
"""
from __future__ import annotations

import json as _json
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Any

from backend.shared.logger import logger

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS mr_tasks (
    id           TEXT PRIMARY KEY,
    inputs_json  TEXT NOT NULL,
    status       TEXT NOT NULL,
    report_md    TEXT,
    trace_id     TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_mr_tasks_created ON mr_tasks(created_at DESC);

CREATE TABLE IF NOT EXISTS mr_evidence (
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


class MarketResearchStore:
    """市场调研任务 + 证据存储：SQLite + threading.Lock。"""

    def __init__(self, db_path: str = "data/market_research.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        with self._lock, self._conn() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()
        logger.debug("market_research store 初始化完成: %s", self._db_path)

    # ── 任务 ────────────────────────────────────
    def create(self, inputs: dict[str, Any]) -> str:
        task_id = uuid.uuid4().hex[:12]
        self.ensure_task(task_id, inputs)
        return task_id

    def ensure_task(self, task_id: str, inputs: dict[str, Any] | None = None) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO mr_tasks
                   (id, inputs_json, status, created_at) VALUES (?, ?, 'running', ?)""",
                (task_id, _json.dumps(inputs or {}, ensure_ascii=False, default=str),
                 datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()

    def update_result(self, task_id: str, *, status: str, report_md: str = "",
                      trace_id: str = "", error: str = "") -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """UPDATE mr_tasks
                   SET status = ?, report_md = ?, trace_id = ?, error = ?, finished_at = ?
                   WHERE id = ?""",
                (status, report_md, trace_id, error,
                 datetime.now().isoformat(timespec="seconds"), task_id),
            )
            conn.commit()

    def list(self, page: int = 1, page_size: int = 20) -> list[dict[str, Any]]:
        offset = (page - 1) * page_size
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """SELECT id, status, trace_id, error, created_at, finished_at, inputs_json
                   FROM mr_tasks ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?""",
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
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM mr_tasks WHERE id = ?", (task_id,)).fetchone()
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
                conn.execute(
                    """INSERT OR REPLACE INTO mr_evidence
                       (task_id, evidence_id, title, url, source_type, raw_text,
                        numbers_json, search_query, fetched_at, published_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (task_id, e["evidence_id"], e.get("title", ""), e.get("url", ""),
                     e.get("source_type", "media"), e.get("raw_text", ""),
                     _json.dumps(e.get("numbers", []), ensure_ascii=False),
                     e.get("search_query", ""), e.get("fetched_at", now),
                     e.get("published_at")),
                )
            conn.commit()
        return len(evidence)

    def list_evidence(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM mr_evidence WHERE task_id = ? ORDER BY evidence_id",
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


_store: MarketResearchStore | None = None


def get_market_research_store() -> MarketResearchStore:
    """模块级单例。"""
    global _store
    if _store is None:
        _store = MarketResearchStore()
    return _store
