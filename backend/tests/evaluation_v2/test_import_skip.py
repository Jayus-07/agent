"""导入「跳过错误行」与重复计数的契约测试。

说明：本文件不包含任何 SQL；repository 的语句全部使用 %s 参数化占位。
count_duplicate_queries 的单测通过 MagicMock 假事务游标模拟命中集合。
"""

import asyncio
import io
import json
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, UploadFile

from backend.app.api.deps import OperatorIdentity
from backend.app.api.identity import Identity
from backend.app.api.routes.evaluation_v2 import (
    import_dataset_candidates,
    preview_dataset_import,
)
from backend.evaluation_v2 import repository
from backend.evaluation_v2.imports import validate_non_rag_upload

_MANIFEST = {
    "schema_version": "evaluation-v2/1.0",
    "dataset_key": "sql_golden",
    "name": "SQL 金标",
    "version": "1.0.0",
    "module": "sql",
}

_GOOD_CASE = {
    "id": "SQL-001",
    "question": "上个月店铺 GMV 是多少",
    "expected": {"should_reject": True, "expected_status": ["no_table"]},
}
_BAD_CASE = {"id": "SQL-002", "question": "坏行缺少 expected"}


def _upload(cases: list[dict]) -> UploadFile:
    body = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in cases)
    return UploadFile(filename="sql-cases.jsonl", file=io.BytesIO(body.encode("utf-8")))


def _identity() -> tuple[Identity, OperatorIdentity]:
    return (
        Identity(user_id="alice", tenant_id="tenant-a"),
        OperatorIdentity(role="editor", actor="user:alice"),
    )


def _stub_transaction(hit_query: str):
    cursor = MagicMock()
    cursor.fetchall.return_value = [{"q": hit_query}]

    @contextmanager
    def transaction(_tenant_id):
        yield None, cursor

    return transaction


def test_validate_non_rag_upload_keeps_valid_cases_when_errors_present():
    result = validate_non_rag_upload(
        "sql-cases.jsonl",
        (json.dumps(_GOOD_CASE, ensure_ascii=False) + "\n" + json.dumps(_BAD_CASE) + "\n").encode("utf-8"),
        dict(_MANIFEST),
    )

    assert result["valid"] is False
    # 校验器自身：case_count=合法行数；rows_count=解析行数。路由层统一为解析/合法双口径
    assert result["case_count"] == 1
    assert result["rows_count"] == 2
    assert [item["id"] for item in result["cases"]] == ["SQL-001"]
    assert len(result["errors"]) == 1


def test_count_duplicate_queries_counts_rows_with_normalization(monkeypatch):
    monkeypatch.setattr(repository, "_transaction", _stub_transaction("上个月店铺gmv是多少"))
    queries = ["上个月店铺GMV是多少", " 新问题 ", "上个月店铺GMV是多少"]

    assert repository.count_duplicate_queries("tenant-a", "sql_golden", queries) == 2


def test_import_preview_reports_duplicates(monkeypatch):
    import backend.app.api.routes.evaluation_v2 as routes

    monkeypatch.setattr(routes.repository, "get_dataset_domain", lambda *_args, **_kwargs: "sql")
    monkeypatch.setattr(routes.repository, "count_duplicate_queries", lambda *_args, **_kwargs: 1)
    identity, actor = _identity()

    result = asyncio.run(preview_dataset_import(
        file=_upload([_GOOD_CASE]),
        manifest=json.dumps(_MANIFEST),
        identity=identity,
        actor=actor,
    ))

    assert result["valid"] is True
    assert result["duplicates"] == 1


def test_import_skips_error_lines_when_requested(monkeypatch):
    import backend.app.api.routes.evaluation_v2 as routes

    monkeypatch.setattr(routes.repository, "get_dataset_domain", lambda *_args, **_kwargs: "sql")
    captured: dict[str, object] = {}

    def fake_bulk(_tenant, _actor, *, source_kind, payloads, request_id):
        captured.update({"count": len(payloads), "kind": source_kind, "request_id": request_id})
        return [{"candidate_id": f"c-{index}"} for index in range(len(payloads))]

    monkeypatch.setattr(routes.repository, "create_candidates_bulk", fake_bulk)
    identity, actor = _identity()

    result = asyncio.run(import_dataset_candidates(
        file=_upload([_GOOD_CASE, _BAD_CASE]),
        manifest=json.dumps(_MANIFEST),
        request_id="req-skip-001",
        skip_errors=True,
        identity=identity,
        actor=actor,
    ))

    assert captured["count"] == 1
    assert result["candidate_count"] == 1
    assert result["skipped"] == 1


def test_import_rejects_error_lines_by_default(monkeypatch):
    import backend.app.api.routes.evaluation_v2 as routes

    monkeypatch.setattr(routes.repository, "get_dataset_domain", lambda *_args, **_kwargs: "sql")

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("默认路径不应创建候选")

    monkeypatch.setattr(routes.repository, "create_candidates_bulk", _forbidden)
    identity, actor = _identity()

    with pytest.raises(HTTPException) as captured:
        asyncio.run(import_dataset_candidates(
            file=_upload([_GOOD_CASE, _BAD_CASE]),
            manifest=json.dumps(_MANIFEST),
            request_id="req-skip-002",
            skip_errors=False,
            identity=identity,
            actor=actor,
        ))

    assert captured.value.status_code == 422


def test_import_skip_without_valid_cases_still_rejected(monkeypatch):
    import backend.app.api.routes.evaluation_v2 as routes

    monkeypatch.setattr(routes.repository, "get_dataset_domain", lambda *_args, **_kwargs: "sql")

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("无合法行时不应创建候选")

    monkeypatch.setattr(routes.repository, "create_candidates_bulk", _forbidden)
    identity, actor = _identity()

    with pytest.raises(HTTPException) as captured:
        asyncio.run(import_dataset_candidates(
            file=_upload([_BAD_CASE]),
            manifest=json.dumps(_MANIFEST),
            request_id="req-skip-003",
            skip_errors=True,
            identity=identity,
            actor=actor,
        ))

    assert captured.value.status_code == 422
