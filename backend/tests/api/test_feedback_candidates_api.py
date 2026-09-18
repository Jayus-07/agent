"""WP5：评测候选 promotion 的跨进程互斥契约。"""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.app.api.deps import OperatorIdentity
from backend.app.api.identity import Identity
from backend.app.api.routes import feedback as feedback_route
from backend.feedback import candidates_pg
from backend.evaluation import curator
from backend.tests.fixtures.pg_env import (  # noqa: F401
    pg_clean_tables,
    pg_iso_env,
)


def _request() -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/feedback/candidates/candidate-1/promote",
        "headers": [],
        "query_string": b"",
    })


def test_promote_candidate_uses_tenant_trace_database_lock(monkeypatch):
    locks: list[tuple[str, str]] = []

    @contextmanager
    def fake_lock(tenant_id: str, trace_id: str):
        locks.append((tenant_id, trace_id))
        yield

    monkeypatch.setattr(feedback_route, "promotion_lock", fake_lock, raising=False)
    monkeypatch.setattr(
        feedback_route,
        "resolve_identity",
        lambda _request: Identity(user_id="admin-1", tenant_id="tenant-a"),
    )
    monkeypatch.setattr(
        feedback_route,
        "get_candidate",
        lambda _candidate_id, _tenant_id: {
            "candidate_id": "candidate-1",
            "tenant_id": "tenant-a",
            "trace_id": "trace-1",
            "status": "approved",
            "case_json": '{"id":"TRACE-1","question":"q",'
                          '"module":"e2e","expected":{},"metadata":{}}',
        },
    )
    monkeypatch.setattr(
        feedback_route,
        "append_case",
        lambda _case: {"appended": True, "reason": "ok"},
    )
    monkeypatch.setattr(
        feedback_route,
        "mark_promoted",
        lambda *_args: {"status": "promoted", "promoted_case_id": "TRACE-1"},
    )

    result = asyncio.run(
        feedback_route.promote_feedback_candidate(
            "candidate-1",
            _request(),
            OperatorIdentity(role="admin", actor="user:admin-1"),
        )
    )

    assert result["status"] == "promoted"
    assert locks == [("tenant-a", "trace-1")]


def test_promote_candidate_does_not_mark_promoted_when_export_fails(monkeypatch):
    @contextmanager
    def fake_lock(_tenant_id: str, _trace_id: str):
        yield

    monkeypatch.setattr(feedback_route, "promotion_lock", fake_lock)
    monkeypatch.setattr(
        feedback_route,
        "resolve_identity",
        lambda _request: Identity(user_id="admin-1", tenant_id="tenant-a"),
    )
    monkeypatch.setattr(
        feedback_route,
        "get_candidate",
        lambda _candidate_id, _tenant_id: {
            "candidate_id": "candidate-1",
            "tenant_id": "tenant-a",
            "trace_id": "trace-1",
            "status": "approved",
            "case_json": '{"id":"TRACE-1","question":"q",'
                          '"module":"e2e","expected":{},"metadata":{}}',
        },
    )
    monkeypatch.setattr(
        feedback_route,
        "append_case",
        lambda _case: {"appended": False, "reason": "写入失败: disk down"},
    )
    promoted_calls: list[tuple] = []
    monkeypatch.setattr(
        feedback_route,
        "mark_promoted",
        lambda *args: promoted_calls.append(args),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            feedback_route.promote_feedback_candidate(
                "candidate-1",
                _request(),
                OperatorIdentity(role="admin", actor="user:admin-1"),
            )
        )

    assert getattr(exc_info.value, "status_code", None) == 503
    assert promoted_calls == []


def test_candidate_review_and_promotion_are_pg_isolated_and_idempotent(
    monkeypatch, tmp_path, pg_clean_tables,
):
    """真实 PG 验收：审核后才能导出，跨租户不可见，重复 promotion 不重复写文件。"""
    monkeypatch.setenv("FEEDBACK_PG_TABLE", "pgtest_biz_feedback_candidate_api")
    candidates_pg.init_db()
    candidates_pg.create_candidate(
        feedback_id=101,
        tenant_id="tenant-a",
        actor_id="user-a",
        trace_id="trace-a",
        module="e2e",
        case_payload={
            "id": "TRACE-A",
            "question": "q",
            "module": "e2e",
            "expected": {"expected_answer": "a"},
            "metadata": {"trace_id": "trace-a"},
        },
    )

    monkeypatch.setattr(
        feedback_route,
        "resolve_identity",
        lambda _request: Identity(user_id="admin-a", tenant_id="tenant-a"),
    )
    operator = OperatorIdentity(role="admin", actor="admin-a")
    # 从真实 PG 读取候选 id，避免测试依赖 UUID 生成细节。
    candidate = candidates_pg.list_candidates("tenant-a")[0]
    approved = asyncio.run(
        feedback_route.approve_feedback_candidate(
            candidate["candidate_id"],
            feedback_route.CandidateReviewRequest(note="人工确认"),
            _request(), operator,
        )
    )
    assert approved["candidate"]["status"] == "approved"

    def append_to_test_dataset(case):
        return curator.append_case(case, dataset_dir=tmp_path)

    monkeypatch.setattr(feedback_route, "append_case", append_to_test_dataset)
    first = asyncio.run(
        feedback_route.promote_feedback_candidate(
            candidate["candidate_id"], _request(), operator,
        )
    )
    second = asyncio.run(
        feedback_route.promote_feedback_candidate(
            candidate["candidate_id"], _request(), operator,
        )
    )
    assert first["status"] == "promoted"
    assert first["appended"] is True
    assert second == {
        "ok": True,
        "status": "promoted",
        "case_id": first["case_id"],
        "appended": False,
    }

    monkeypatch.setattr(
        feedback_route,
        "resolve_identity",
        lambda _request: Identity(user_id="admin-b", tenant_id="tenant-b"),
    )
    other_tenant = asyncio.run(
        feedback_route.get_feedback_candidates(_request(), "", 50, operator)
    )
    assert other_tenant["items"] == []

    stored = candidates_pg.get_candidate(candidate["candidate_id"], "tenant-a")
    assert stored["status"] == "promoted"
    lines = (tmp_path / "e2e" / "cases.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["metadata"]["trace_id"] == "trace-a"
