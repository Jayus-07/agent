"""PostgresSelectionDecisionStore — 选品决策两表的 PostgreSQL 连接层（迁移计划 Batch D）。

与 SQLite 版 `SelectionDecisionStore` 对外接口完全一致。
PG 为唯一实现（2026-09-17 SQLite 轨删除）。

库归属：agent_business（业务数据，对 NL2SQL 可见）。
schema 与 backend/sql/migrations/016_business_stores_pg.sql 保持一致
（decision_log 快照不可变由该迁移中的触发器兜底，语义与 SQLite 版触发器一致）。

方言映射：
  - `?`                    → `%s`
  - `INSERT OR IGNORE`     → `INSERT ... ON CONFLICT (id) DO NOTHING`
  - `rowid`（tie-breaker） → `ctid`（同秒排序稳定性，物理写入序近似）
  - sqlite3 隐式事务       → psycopg2 显式 commit/rollback（per-op 连接）
"""

from __future__ import annotations

import json as _json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Iterator

import psycopg2
import psycopg2.extras

from backend.config.database import SELECTION_DECISION_PG_CONFIG
from backend.selection_decision.store import SelectionDecisionStore
from backend.shared.logger import logger

_PREFIX = os.getenv("SELECTION_DECISION_PG_TABLE_PREFIX", "")
_T_TASKS = f"{_PREFIX}selection_tasks"
_T_DLOG = f"{_PREFIX}decision_log"

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {_T_TASKS} (
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
CREATE INDEX IF NOT EXISTS idx_sd_tasks_created ON {_T_TASKS}(created_at DESC);

CREATE TABLE IF NOT EXISTS {_T_DLOG} (
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
CREATE INDEX IF NOT EXISTS idx_dlog_candidate ON {_T_DLOG}(candidate_id, decision_version DESC);
"""


class PostgresSelectionDecisionStore(SelectionDecisionStore):
    """选品决策任务存储 — PostgreSQL 实现（isinstance 兼容）。"""

    def __init__(self, db_path: str = "data/selection_decision.db"):
        self._db_path = db_path  # 兼容保留
        self._lock = threading.Lock()
        self._init_db()
        logger.debug("[PostgresSelectionDecisionStore] 初始化完成（agent_business）")

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        conn = psycopg2.connect(**SELECTION_DECISION_PG_CONFIG)
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

    def create(self, inputs: dict[str, Any]) -> str:
        """创建任务，初始状态 running，返回任务 id。"""
        task_id = uuid.uuid4().hex[:12]
        self.ensure_task(task_id, inputs)
        return task_id

    def ensure_task(self, task_id: str, inputs: dict[str, Any] | None = None) -> None:
        """确保指定 task_id 存在（不存在则按该 id 插入 running 行）"""
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

    def update_result(self, task_id: str, *, status: str, verdict: str = "",
                      report_md: str = "", trace_id: str = "", error: str = "") -> None:
        """Workflow 结束后回写结果，同时记录 finished_at。"""
        with self._lock, self._conn() as conn:
            self._exec(
                conn,
                f"""UPDATE {_T_TASKS}
                   SET status = %s, verdict = %s, report_md = %s, trace_id = %s,
                       error = %s, finished_at = %s
                   WHERE id = %s""",
                (
                    status, verdict, report_md, trace_id, error,
                    datetime.now().isoformat(timespec="seconds"), task_id,
                ),
            )

    def mark_stale_running_failed(self, older_than_sec: int = 300) -> int:
        """把超龄的 running 行标记为 failed（服务重启导致任务中断），返回处理行数。"""
        cutoff = (datetime.now() - timedelta(seconds=older_than_sec)).isoformat(
            timespec="seconds")
        with self._lock, self._conn() as conn:
            cur = self._exec(
                conn,
                f"""UPDATE {_T_TASKS}
                   SET status = 'failed', error = '服务重启，任务中断',
                       finished_at = %s
                   WHERE status = 'running' AND created_at <= %s""",
                (datetime.now().isoformat(timespec="seconds"), cutoff),
            )
        return cur.rowcount

    def list(self, page: int = 1, page_size: int = 20) -> list[dict[str, Any]]:
        """分页列出任务（不含 report_md 大字段），按创建时间倒序。"""
        offset = (page - 1) * page_size
        with self._conn() as conn:
            rows = self._exec(
                conn,
                f"""SELECT id, status, verdict, trace_id, error, created_at, finished_at,
                           inputs_json
                    FROM {_T_TASKS}
                    ORDER BY created_at DESC, ctid DESC
                    LIMIT %s OFFSET %s""",
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

    # ==================== decision_log（决策留痕与反馈闭环） ====================

    def record_decision(self, *, task_id: str | None, candidate_id: str,
                        category: str | None, evidence_snapshot: dict[str, Any],
                        score_snapshot: dict[str, Any], recommendation: str) -> str:
        """记录一次决策（快照不可变）。同一 candidate 自动递增 decision_version。"""
        decision_id = uuid.uuid4().hex[:12]
        with self._lock, self._conn() as conn:
            row = self._exec(
                conn,
                f"SELECT COALESCE(MAX(decision_version), 0) AS v FROM {_T_DLOG} "
                "WHERE candidate_id = %s",
                (candidate_id,),
            ).fetchone()
            version = (row["v"] if row else 0) + 1
            self._exec(
                conn,
                f"""INSERT INTO {_T_DLOG}
                   (decision_id, task_id, candidate_id, category, decision_version,
                    evidence_snapshot, score_snapshot, recommendation, decision_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    decision_id, task_id, candidate_id, category, version,
                    _json.dumps(evidence_snapshot, ensure_ascii=False, default=str),
                    _json.dumps(score_snapshot, ensure_ascii=False, default=str),
                    recommendation,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
        logger.info(f"[PostgresSelectionDecisionStore] 决策留痕 {decision_id} v{version} "
                    f"candidate={candidate_id} recommendation={recommendation}")
        return decision_id

    def set_user_decision(self, decision_id: str, user_decision: str) -> bool:
        """回填用户拍板（adopted / rejected / deferred）。"""
        with self._lock, self._conn() as conn:
            cur = self._exec(
                conn,
                f"UPDATE {_T_DLOG} SET user_decision = %s WHERE decision_id = %s",
                (user_decision, decision_id),
            )
        return cur.rowcount > 0

    def set_feedback(self, decision_id: str, actual_metrics: dict[str, Any]) -> bool:
        """回填事后真实表现，同时记 feedback_at。"""
        with self._lock, self._conn() as conn:
            cur = self._exec(
                conn,
                f"""UPDATE {_T_DLOG} SET actual_metrics = %s, feedback_at = %s
                   WHERE decision_id = %s""",
                (
                    _json.dumps(actual_metrics, ensure_ascii=False, default=str),
                    datetime.now().isoformat(timespec="seconds"),
                    decision_id,
                ),
            )
        return cur.rowcount > 0

    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = self._exec(
                conn, f"SELECT * FROM {_T_DLOG} WHERE decision_id = %s", (decision_id,)
            ).fetchone()
        return self._decision_row_to_dict(row) if row else None

    def list_decisions(self, candidate_id: str | None = None,
                       limit: int = 50) -> list[dict[str, Any]]:
        """列出决策记录（默认全量倒序；传 candidate_id 则按该候选过滤）。"""
        with self._conn() as conn:
            if candidate_id:
                rows = self._exec(
                    conn,
                    f"""SELECT * FROM {_T_DLOG} WHERE candidate_id = %s
                       ORDER BY decision_version DESC LIMIT %s""",
                    (candidate_id, limit),
                ).fetchall()
            else:
                rows = self._exec(
                    conn,
                    f"SELECT * FROM {_T_DLOG} ORDER BY decision_at DESC, ctid DESC LIMIT %s",
                    (limit,),
                ).fetchall()
        return [self._decision_row_to_dict(r) for r in rows]

    def list_decisions_by_task(self, task_id: str,
                               limit: int = 100) -> list[dict[str, Any]]:
        """列出某任务下的全部决策记录（B1 API 专用查询）。"""
        with self._conn() as conn:
            rows = self._exec(
                conn,
                f"""SELECT * FROM {_T_DLOG} WHERE task_id = %s
                   ORDER BY decision_at DESC, ctid DESC LIMIT %s""",
                (task_id, limit),
            ).fetchall()
        return [self._decision_row_to_dict(r) for r in rows]
