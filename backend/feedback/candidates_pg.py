"""反馈生成的评测候选审核仓储。

候选表是审核状态的权威来源；JSONL 评测集只接受 approved 候选的 promotion。
表名跟随 FEEDBACK_PG_TABLE 派生，测试和多实例不会把候选写回生产表。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg2
import psycopg2.extras

from backend.config.database import FEEDBACK_PG_CONFIG


VALID_STATUSES = frozenset({"pending", "approved", "rejected", "promoted"})
_TRANSITIONS = {
    "pending": frozenset({"approved", "rejected"}),
    "approved": frozenset({"promoted"}),
    "rejected": frozenset(),
    "promoted": frozenset(),
}


def _record_candidate_metric(action: str, result: str) -> None:
    """记录候选审核指标；观测故障不得改变审核事务。"""
    try:
        from backend.observability import metrics

        metrics.feedback_candidate_total.labels(
            action=action, result=result
        ).inc()
    except Exception:
        return


def _table() -> str:
    explicit = os.getenv("FEEDBACK_CANDIDATE_PG_TABLE", "").strip()
    if explicit:
        return explicit
    return f"{os.getenv('FEEDBACK_PG_TABLE', 'feedback')}_candidates"


@contextmanager
def _conn() -> Iterator[Any]:
    conn = psycopg2.connect(**FEEDBACK_PG_CONFIG)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _utc_now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


@contextmanager
def promotion_lock(tenant_id: str, trace_id: str) -> Iterator[None]:
    """为同租户同 Trace 的 promotion 提供跨进程事务锁。

    候选状态和 JSONL 文件不在同一事务里，不能只依赖进程内写锁；
    advisory xact lock 让多个 app/worker 实例串行化「读取 approved → 导出
    → 标记 promoted」窗口。进程在导出中崩溃时事务释放，候选仍是 approved，
    下一次 promotion 可安全重试。
    """
    if not tenant_id or not trace_id:
        raise ValueError("promotion lock 必须具备 tenant_id 和 trace_id")
    lock_key = f"feedback-promotion:{tenant_id}:{trace_id}"
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (lock_key,),
            )
        yield


def init_db() -> None:
    table = _table()
    with _conn() as conn:
        conn.cursor().execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
                candidate_id     TEXT PRIMARY KEY,
                feedback_id      BIGINT NOT NULL,
                tenant_id        TEXT NOT NULL,
                actor_id         TEXT NOT NULL,
                trace_id         TEXT NOT NULL,
                module           TEXT NOT NULL,
                case_json        TEXT NOT NULL,
                status           TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','approved','rejected','promoted')),
                reviewer_id      TEXT DEFAULT '',
                review_note      TEXT DEFAULT '',
                promoted_case_id TEXT DEFAULT '',
                created_at       TEXT NOT NULL,
                updated_at       TEXT NOT NULL,
                UNIQUE (tenant_id, trace_id)
            );
            CREATE INDEX IF NOT EXISTS idx_{table}_tenant_status
                ON {table}(tenant_id, status, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_{table}_trace
                ON {table}(tenant_id, trace_id);
        """)


def _select_columns() -> str:
    return (
        "candidate_id, feedback_id, tenant_id, actor_id, trace_id, module, "
        "case_json, status, reviewer_id, review_note, promoted_case_id, "
        "created_at, updated_at"
    )


def create_candidate(
    *,
    feedback_id: int,
    tenant_id: str,
    actor_id: str,
    trace_id: str,
    module: str,
    case_payload: dict[str, Any],
) -> dict[str, Any]:
    """创建 pending 候选；同租户同 Trace 重复反馈返回原候选。"""
    if not tenant_id or not actor_id or not trace_id:
        raise ValueError("候选必须具备 tenant_id、actor_id 和 trace_id")
    candidate_id = uuid.uuid4().hex
    now = _utc_now_text()
    table = _table()
    columns = _select_columns()
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"""INSERT INTO {table}
                (candidate_id, feedback_id, tenant_id, actor_id, trace_id,
                 module, case_json, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)
                ON CONFLICT (tenant_id, trace_id) DO NOTHING
                RETURNING {columns}""",
            (
                candidate_id, feedback_id, tenant_id, actor_id, trace_id,
                module, json.dumps(case_payload, ensure_ascii=False), now, now,
            ),
        )
        row = cur.fetchone()
        if row is None:
            _record_candidate_metric("create", "duplicate")
            cur.execute(
                f"""SELECT {columns} FROM {table}
                       WHERE tenant_id=%s AND trace_id=%s""",
                (tenant_id, trace_id),
            )
            row = cur.fetchone()
        else:
            _record_candidate_metric("create", "pending")
    return dict(row)


def get_candidate(candidate_id: str, tenant_id: str) -> dict[str, Any] | None:
    table = _table()
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"""SELECT {_select_columns()} FROM {table}
                   WHERE candidate_id=%s AND tenant_id=%s""",
            (candidate_id, tenant_id),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def list_candidates(
    tenant_id: str, status: str = "", limit: int = 50,
) -> list[dict[str, Any]]:
    if status and status not in VALID_STATUSES:
        raise ValueError("非法候选状态")
    table = _table()
    clauses = ["tenant_id=%s"]
    params: list[Any] = [tenant_id]
    if status:
        clauses.append("status=%s")
        params.append(status)
    params.append(max(1, min(limit, 200)))
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"""SELECT candidate_id, feedback_id, tenant_id, actor_id, trace_id,
                      module, status, reviewer_id, review_note, case_json,
                      promoted_case_id, created_at, updated_at
                   FROM {table}
                   WHERE {' AND '.join(clauses)}
                   ORDER BY updated_at DESC LIMIT %s""",
            tuple(params),
        )
        items = []
        for row in cur.fetchall():
            item = dict(row)
            try:
                case_payload = json.loads(item.pop("case_json") or "{}")
            except (TypeError, ValueError):
                case_payload = {}
                item.pop("case_json", None)
            metadata = case_payload.get("metadata") or {}
            item["correction_text"] = metadata.get("correction_text", "")
            item["expected_answer"] = case_payload.get("expected_answer", "")
            item["summary"] = case_payload.get("question", "")
            item["reviewer"] = item.get("reviewer_id", "")
            items.append(item)
        return items


def transition_candidate(
    candidate_id: str,
    tenant_id: str,
    target_status: str,
    reviewer_id: str,
    review_note: str = "",
) -> dict[str, Any]:
    """执行受限状态迁移，并对重复相同结果保持幂等。"""
    if target_status not in VALID_STATUSES:
        raise ValueError("非法候选状态")
    table = _table()
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"SELECT * FROM {table} WHERE candidate_id=%s AND tenant_id=%s FOR UPDATE",
            (candidate_id, tenant_id),
        )
        row = cur.fetchone()
        if row is None:
            raise LookupError("候选不存在")
        current = row["status"]
        if current == target_status:
            _record_candidate_metric("review", "replay")
            return dict(row)
        if target_status not in _TRANSITIONS[current]:
            raise ValueError(f"非法候选状态迁移: {current} -> {target_status}")
        now = _utc_now_text()
        cur.execute(
            f"""UPDATE {table}
                   SET status=%s, reviewer_id=%s, review_note=%s, updated_at=%s
                 WHERE candidate_id=%s AND tenant_id=%s
             RETURNING *""",
            (
                target_status, reviewer_id[:128], review_note[:1000], now,
                candidate_id, tenant_id,
            ),
        )
        _record_candidate_metric("review", target_status)
        return dict(cur.fetchone())


def mark_promoted(
    candidate_id: str,
    tenant_id: str,
    case_id: str,
    reviewer_id: str,
) -> dict[str, Any]:
    table = _table()
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"""UPDATE {table}
                   SET status='promoted', promoted_case_id=%s,
                       reviewer_id=%s, updated_at=%s
                 WHERE candidate_id=%s AND tenant_id=%s AND status='approved'
             RETURNING *""",
            (case_id, reviewer_id[:128], _utc_now_text(), candidate_id, tenant_id),
        )
        row = cur.fetchone()
        if row is not None:
            _record_candidate_metric("promote", "promoted")
            return dict(row)
    existing = get_candidate(candidate_id, tenant_id)
    if existing and existing["status"] == "promoted":
        _record_candidate_metric("promote", "replay")
        return existing
    _record_candidate_metric("promote", "rejected")
    raise ValueError("候选当前不允许 promotion")


__all__ = [
    "VALID_STATUSES",
    "create_candidate",
    "get_candidate",
    "init_db",
    "list_candidates",
    "mark_promoted",
    "promotion_lock",
    "transition_candidate",
]
