"""selection_decision/store.py — 选品决策任务持久化

表结构对齐 workflow_runs 惯例：SQLite 单表 + threading.Lock。
列表接口不返回 report_md 大字段，详情接口才返回。
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
CREATE TABLE IF NOT EXISTS selection_tasks (
    id           TEXT PRIMARY KEY,
    inputs_json  TEXT NOT NULL,
    status       TEXT NOT NULL,
    verdict      TEXT,
    report_md    TEXT,
    trace_id     TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_sd_tasks_created ON selection_tasks(created_at DESC);
"""


class SelectionDecisionStore:
    """选品决策任务存储：SQLite 单表 + threading.Lock。"""

    def __init__(self, db_path: str = "data/selection_decision.db"):
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
        logger.debug("selection_decision store 初始化完成: %s", self._db_path)

    def create(self, inputs: dict[str, Any]) -> str:
        """创建任务，初始状态 running，返回任务 id。"""
        task_id = uuid.uuid4().hex[:12]
        with self._lock, self._conn() as conn:
            conn.execute(
                """INSERT INTO selection_tasks
                   (id, inputs_json, status, created_at) VALUES (?, ?, 'running', ?)""",
                (task_id, _json.dumps(inputs, ensure_ascii=False, default=str),
                 datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()
        return task_id

    def update_result(self, task_id: str, *, status: str, verdict: str = "",
                      report_md: str = "", trace_id: str = "", error: str = "") -> None:
        """Workflow 结束后回写结果，同时记录 finished_at。"""
        with self._lock, self._conn() as conn:
            conn.execute(
                """UPDATE selection_tasks
                   SET status = ?, verdict = ?, report_md = ?, trace_id = ?,
                       error = ?, finished_at = ?
                   WHERE id = ?""",
                (status, verdict, report_md, trace_id, error,
                 datetime.now().isoformat(timespec="seconds"), task_id),
            )
            conn.commit()

    def ensure_task(self, task_id: str, inputs: dict[str, Any] | None = None) -> None:
        """确保指定 task_id 存在（不存在则按该 id 插入 running 行），供直跑场景补建"""
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT id FROM selection_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                conn.execute(
                    """INSERT INTO selection_tasks
                       (id, inputs_json, status, created_at) VALUES (?, ?, 'running', ?)""",
                    (task_id, _json.dumps(inputs or {}, ensure_ascii=False, default=str),
                     datetime.now().isoformat(timespec="seconds")),
                )
                conn.commit()

    def list(self, page: int = 1, page_size: int = 20) -> list[dict[str, Any]]:
        """分页列出任务（不含 report_md 大字段），按创建时间倒序。"""
        offset = (page - 1) * page_size
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """SELECT id, status, verdict, trace_id, error, created_at, finished_at,
                          inputs_json
                   FROM selection_tasks
                   ORDER BY created_at DESC, rowid DESC
                   LIMIT ? OFFSET ?""",
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
        """按 id 获取任务详情（含 report_md），不存在返回 None。"""
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM selection_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        try:
            d["inputs"] = _json.loads(d.pop("inputs_json"))
        except (TypeError, ValueError):
            d["inputs"] = {}
        return d


_store: SelectionDecisionStore | None = None


def get_selection_decision_store() -> SelectionDecisionStore:
    """模块级单例。"""
    global _store
    if _store is None:
        _store = SelectionDecisionStore()
    return _store
