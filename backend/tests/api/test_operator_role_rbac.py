"""test_operator_role_rbac.py — 运营角色双通道 + 审批/提权 RBAC（2026-09-16）

覆盖：
- resolve_operator_role：JWT 通道（X-User-Id + X-User-Roles，多角色取最高）、
  服务凭据通道（X-Internal-Token → admin）、两通道皆无 → 401/503
- approvals approve/reject：仅 admin 可处置（viewer → 403）
- PATCH /sys/users/{id}/role：仅 admin、角色枚举校验、404

approvals/auth_local 的外部协作（decide_request、DB 会话）全部打桩，
只测路由层的角色判定与响应语义。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.api.deps import OperatorIdentity, resolve_operator_role
from backend.app.api.routes import approvals as approvals_mod
from backend.app.api.routes import auth_local
from backend.app.api.routes.approvals import router as approvals_router
from backend.app.api.routes.auth_local import sys_router


# ── resolve_operator_role：JWT 通道 ──────────────────────────

class _FakeRequest:
    def __init__(self, headers: dict | None = None):
        self.headers = headers or {}


@pytest.mark.anyio
async def test_operator_jwt_channel_takes_highest_role():
    req = _FakeRequest({"X-User-Id": "15", "X-Auth-Type": "jwt", "X-User-Roles": "viewer,editor,admin"})
    op = await resolve_operator_role(req)
    assert op.role == "admin"
    assert op.actor == "user:15"


@pytest.mark.anyio
async def test_operator_jwt_channel_viewer_stays_viewer():
    req = _FakeRequest({"X-User-Id": "15", "X-User-Roles": "viewer"})
    op = await resolve_operator_role(req)
    assert op.role == "viewer"


@pytest.mark.anyio
async def test_operator_jwt_unknown_roles_falls_back_to_service_token(monkeypatch):
    """旧令牌（无 roles claim）或未知角色 → 落到服务凭据通道，不冒充任何用户角色"""
    async def _fake_internal(request):
        return None

    monkeypatch.setattr("backend.app.api.deps.require_internal_token", _fake_internal)
    req = _FakeRequest({"X-User-Id": "15", "X-User-Roles": "unknown-role"})
    op = await resolve_operator_role(req)
    assert op.role == "admin"
    assert op.actor == "service:internal-token"


@pytest.mark.anyio
async def test_operator_no_credentials_raises(monkeypatch):
    async def _deny(request):
        raise HTTPException(status_code=401, detail="缺少内部令牌")

    monkeypatch.setattr("backend.app.api.deps.require_internal_token", _deny)
    with pytest.raises(HTTPException) as exc:
        await resolve_operator_role(_FakeRequest())
    assert exc.value.status_code == 401


# ── approvals：处置仅 admin ──────────────────────────────────

def _approvals_client(monkeypatch, role: str) -> TestClient:
    app = FastAPI()
    app.include_router(approvals_router)
    app.dependency_overrides[resolve_operator_role] = lambda: OperatorIdentity(
        role=role, actor="user:15" if role != "admin" else "user:1")
    client = TestClient(app, raise_server_exceptions=False)
    # decide_request 打桩：返回一条已批准记录
    monkeypatch.setattr(approvals_mod, "decide_request",
                        lambda rid, approve, reviewer, reason: {
                            "id": rid, "status": "approved" if approve else "rejected",
                            "reviewer": reviewer, "reason": reason})
    return client


@pytest.mark.parametrize("role,expect", [("viewer", 403), ("editor", 403), ("admin", 200)])
def test_approve_requires_admin(monkeypatch, role, expect):
    client = _approvals_client(monkeypatch, role)
    res = client.post("/approvals/req-1/approve", json={"reason": "已核对"})
    assert res.status_code == expect
    if expect == 200:
        body = res.json()
        assert body["ok"] is True and body["request"]["reviewer"] == "user:1"


def test_reject_requires_admin(monkeypatch):
    client = _approvals_client(monkeypatch, "viewer")
    res = client.post("/approvals/req-1/reject", json={"reason": "参数越界"})
    assert res.status_code == 403


def test_approve_reason_passthrough(monkeypatch):
    client = _approvals_client(monkeypatch, "admin")
    res = client.post("/approvals/req-1/approve", json={"reason": "SQL 已核对"})
    assert res.status_code == 200
    assert res.json()["request"]["reason"] == "SQL 已核对"


# ── PATCH /sys/users/{id}/role：提权接口 ─────────────────────

class _FakeResult:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _FakeSession:
    """最小 async session：execute 返回预设行，commit 记账"""

    def __init__(self, row):
        self._row = row
        self.committed = False

    async def execute(self, _sql, _params=None):
        return _FakeResult(self._row)

    async def commit(self):
        self.committed = True


def _auth_client(monkeypatch, role: str, db_row) -> TestClient:
    app = FastAPI()
    app.include_router(sys_router)
    app.dependency_overrides[resolve_operator_role] = lambda: OperatorIdentity(role=role, actor="user:1")

    import contextlib

    @contextlib.asynccontextmanager
    async def _fake_db():
        yield _FakeSession(db_row)

    monkeypatch.setattr(auth_local, "_db", _fake_db)
    return TestClient(app, raise_server_exceptions=False)


def test_change_role_rejects_non_admin(monkeypatch):
    client = _auth_client(monkeypatch, "viewer", None)
    res = client.patch("/sys/users/5/role", json={"role": "admin"})
    assert res.status_code == 403


def test_change_role_rejects_unknown_role_value(monkeypatch):
    client = _auth_client(monkeypatch, "admin", {"id": 5, "username": "u5", "role": "superuser"})
    res = client.patch("/sys/users/5/role", json={"role": "superuser"})
    assert res.status_code == 400
    assert "viewer/editor/admin" in res.json()["message"]


def test_change_role_ok(monkeypatch):
    client = _auth_client(monkeypatch, "admin", {"id": 5, "username": "u5", "role": "editor"})
    res = client.patch("/sys/users/5/role", json={"role": "editor"})
    assert res.status_code == 200
    body = res.json()["data"]
    assert body["role"] == "editor" and body["changedBy"] == "user:1"


def test_change_role_missing_user_404(monkeypatch):
    client = _auth_client(monkeypatch, "admin", None)
    res = client.patch("/sys/users/999/role", json={"role": "editor"})
    assert res.status_code == 404
