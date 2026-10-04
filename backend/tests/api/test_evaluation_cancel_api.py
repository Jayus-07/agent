"""评测取消端点 API 回归测试（C2-1/RUN-03/04 + C2-6/REL-09）。

鉴权依赖以固定管理员替身覆写（deps 层自有测试覆盖鉴权本体）；
DATA_ROOT 指向临时目录，审计走 monkeypatch 假捕获（DB 层由
test_run_lifecycle_cancel 与实机验证覆盖）。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from backend.evaluation import storage as storage_mod
    from backend.app.api.routes import evaluation as evaluation_routes

    root = tmp_path / "eval_runs"
    root.mkdir()
    monkeypatch.setattr(storage_mod, "DATA_ROOT", root)
    # routes 模块 from-import 了符号，需一并替换引用
    monkeypatch.setattr(evaluation_routes, "list_runs", storage_mod.list_runs)
    monkeypatch.setattr(evaluation_routes, "load_report", storage_mod.load_report)
    monkeypatch.setattr(
        evaluation_routes, "read_run_status", storage_mod.read_run_status,
    )
    monkeypatch.setattr(
        evaluation_routes, "request_cancel", storage_mod.request_cancel,
    )
    monkeypatch.setattr(
        evaluation_routes, "validate_run_id", storage_mod.validate_run_id,
    )

    audit_rows: list[dict] = []
    import backend.evaluation.audit as audit_mod

    # cancel/operations 端点函数内 import audit 模块符号
    real_record = audit_mod.record_operation
    real_list = audit_mod.list_operations

    def _fake_record(op, tid, **kw):
        audit_rows.append({"operation": op, "target_id": tid, **kw})
        return True

    monkeypatch.setattr(audit_mod, "record_operation", _fake_record)
    monkeypatch.setattr(
        audit_mod, "list_operations",
        lambda tid, limit=20: list(reversed(audit_rows)),
    )

    app = FastAPI()
    app.include_router(evaluation_routes.router, prefix="/api")
    from backend.app.api.deps import OperatorIdentity, require_admin_user

    app.dependency_overrides[require_admin_user] = lambda: OperatorIdentity(
        role="admin", actor="user:test-admin",
    )
    client = TestClient(app)
    client.audit_rows = audit_rows  # type: ignore[attr-defined]
    yield client
    monkeypatch.setattr(audit_mod, "record_operation", real_record)
    monkeypatch.setattr(audit_mod, "list_operations", real_list)


def test_cancel_run_writes_request_and_audit(client, tmp_path):
    from backend.evaluation import storage as storage_mod

    storage_mod.mark_run_status("r1", "running")
    resp = client.post("/api/evaluation/runs/r1/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["cancel_requested"] is True
    assert body["audit_recorded"] is True
    assert storage_mod.is_cancel_requested("r1") is True
    assert client.audit_rows[0]["operation"] == "eval_run.cancel"
    assert client.audit_rows[0]["target_id"] == "r1"
    assert "test-admin" in client.audit_rows[0]["actor"]


def test_cancel_is_idempotent(client):
    from backend.evaluation import storage as storage_mod

    storage_mod.mark_run_status("r2", "running")
    first = client.post("/api/evaluation/runs/r2/cancel")
    second = client.post("/api/evaluation/runs/r2/cancel")
    assert first.status_code == second.status_code == 200
    assert second.json()["cancel_requested"] is True
    # RUN-04：重复取消不产生重复副作用（审计可多行但取消请求文件唯一、状态一致）
    assert storage_mod.is_cancel_requested("r2") is True


def test_cancel_invalid_run_id_rejected(client):
    # validate_run_id 只允许字母/数字开头的安全字符；~ 开头非法
    resp = client.post("/api/evaluation/runs/~bad_id/cancel")
    assert resp.status_code == 422


def test_run_operations_listing(client):
    from backend.evaluation import storage as storage_mod

    storage_mod.mark_run_status("r3", "running")
    client.post("/api/evaluation/runs/r3/cancel")
    resp = client.get("/api/evaluation/runs/r3/operations")
    assert resp.status_code == 200
    ops = resp.json()["operations"]
    assert ops and ops[0]["operation"] == "eval_run.cancel"
