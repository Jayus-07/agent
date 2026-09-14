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
from datetime import datetime, timedelta
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

-- 批次3：decision_log — 决策留痕与结果反馈闭环（计划书批次3 数据模型定稿）
-- 不可变原则：evidence_snapshot / score_snapshot 写入后禁止 UPDATE；
-- 后续改权重/重抓数据一律新增 decision_version 行（见触发器 + 应用层只读封装）
CREATE TABLE IF NOT EXISTS decision_log (
    decision_id       TEXT PRIMARY KEY,
    task_id           TEXT,
    candidate_id      TEXT NOT NULL,
    category          TEXT,
    decision_version  INTEGER NOT NULL DEFAULT 1,
    evidence_snapshot TEXT NOT NULL,
    score_snapshot    TEXT NOT NULL,
    recommendation    TEXT NOT NULL,
    user_decision     TEXT,
    decision_at       TEXT NOT NULL,
    actual_metrics    TEXT,
    feedback_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_dlog_candidate ON decision_log(candidate_id, decision_version DESC);
CREATE TRIGGER IF NOT EXISTS trg_decision_log_snapshot_immutable
BEFORE UPDATE ON decision_log
WHEN OLD.evidence_snapshot IS NOT NEW.evidence_snapshot
  OR OLD.score_snapshot IS NOT NEW.score_snapshot
BEGIN
    SELECT RAISE(ABORT, 'decision_log 快照不可变：evidence_snapshot/score_snapshot 禁止更新，请新增 decision_version 行');
END;
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
            conn.execute(
                """INSERT OR IGNORE INTO selection_tasks
                   (id, inputs_json, status, created_at) VALUES (?, ?, 'running', ?)""",
                (task_id, _json.dumps(inputs or {}, ensure_ascii=False, default=str),
                 datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()

    def mark_stale_running_failed(self, older_than_sec: int = 300) -> int:
        """把超龄的 running 行标记为 failed（服务重启导致任务中断），返回处理行数。

        created_at 为 ISO 字符串，可直接字典序比较。
        """
        cutoff = (datetime.now() - timedelta(seconds=older_than_sec)).isoformat(
            timespec="seconds")
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """UPDATE selection_tasks
                   SET status = 'failed', error = '服务重启，任务中断',
                       finished_at = ?
                   WHERE status = 'running' AND created_at <= ?""",
                (datetime.now().isoformat(timespec="seconds"), cutoff),
            )
            conn.commit()
        if cur.rowcount:
            logger.info(f"[SelectionDecision:store] 标记 {cur.rowcount} 个 stale running 任务为 failed")
        return cur.rowcount

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

    # ==================== decision_log（批次3：决策留痕与反馈闭环） ====================
    # 证据/评分快照一旦写入不可变（触发器兜底）；可变字段仅限
    # user_decision / actual_metrics / feedback_at，且各有专用方法。

    def record_decision(self, *, task_id: str | None, candidate_id: str,
                        category: str | None, evidence_snapshot: dict[str, Any],
                        score_snapshot: dict[str, Any], recommendation: str) -> str:
        """记录一次决策（快照不可变）。同一 candidate 自动递增 decision_version。"""
        decision_id = uuid.uuid4().hex[:12]
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(decision_version), 0) AS v FROM decision_log WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
            version = (row["v"] if row else 0) + 1
            conn.execute(
                """INSERT INTO decision_log
                   (decision_id, task_id, candidate_id, category, decision_version,
                    evidence_snapshot, score_snapshot, recommendation, decision_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (decision_id, task_id, candidate_id, category, version,
                 _json.dumps(evidence_snapshot, ensure_ascii=False, default=str),
                 _json.dumps(score_snapshot, ensure_ascii=False, default=str),
                 recommendation,
                 datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()
        logger.info(f"[SelectionDecision:store] 决策留痕 {decision_id} v{version} "
                    f"candidate={candidate_id} recommendation={recommendation}")
        return decision_id

    def set_user_decision(self, decision_id: str, user_decision: str) -> bool:
        """回填用户拍板（adopted / rejected / deferred）。"""
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "UPDATE decision_log SET user_decision = ? WHERE decision_id = ?",
                (user_decision, decision_id),
            )
            conn.commit()
        return cur.rowcount > 0

    def set_feedback(self, decision_id: str, actual_metrics: dict[str, Any]) -> bool:
        """回填事后真实表现（销量/评价/收益等），同时记 feedback_at。"""
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """UPDATE decision_log SET actual_metrics = ?, feedback_at = ?
                   WHERE decision_id = ?""",
                (_json.dumps(actual_metrics, ensure_ascii=False, default=str),
                 datetime.now().isoformat(timespec="seconds"), decision_id),
            )
            conn.commit()
        return cur.rowcount > 0

    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM decision_log WHERE decision_id = ?", (decision_id,)
            ).fetchone()
        return self._decision_row_to_dict(row) if row else None

    def list_decisions(self, candidate_id: str | None = None,
                       limit: int = 50) -> list[dict[str, Any]]:
        """列出决策记录（默认全量倒序；传 candidate_id 则按该候选过滤）。"""
        with self._lock, self._conn() as conn:
            if candidate_id:
                rows = conn.execute(
                    """SELECT * FROM decision_log WHERE candidate_id = ?
                       ORDER BY decision_version DESC LIMIT ?""",
                    (candidate_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM decision_log ORDER BY decision_at DESC, rowid DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._decision_row_to_dict(r) for r in rows]

    @staticmethod
    def _decision_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        for key in ("evidence_snapshot", "score_snapshot", "actual_metrics"):
            try:
                d[key] = _json.loads(d[key]) if d.get(key) else {}
            except (TypeError, ValueError):
                d[key] = {}
        return d


_store: SelectionDecisionStore | None = None


def get_selection_decision_store() -> SelectionDecisionStore:
    """模块级单例。"""
    global _store
    if _store is None:
        _store = SelectionDecisionStore()
    return _store
