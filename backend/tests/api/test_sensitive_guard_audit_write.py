"""敏感守卫 audit 灰度只放宽只读（2026-11-08 安全审计修复）。

指纹：``sensitive-api-guard.audit-non-admin-bypass``。

修复前 audit 分支把 kind/角色判定整体降级为 warning 放行：切到 audit
后，服务凭据可执行任务中心 retry/revoke/reexecute 等写操作。本文件锁死
「audit 只放宽只读」：GET 放行、POST 写操作 403（guard 直调 + 真实路由）。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.app.api.deps import OperatorIdentity
from backend.app.api.routes import admin_tasks, observability


def _request(method: str, path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": [],
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


@pytest.fixture(autouse=True)
def _audit_mode(monkeypatch):
    # 本文件验证 env 灰度；清掉配置 API 可能留下的 DB 覆盖值。
    monkeypatch.setattr("backend.services.sys_config._values", {})
    monkeypatch.setenv("SENSITIVE_API_GUARD_MODE", "audit")


@pytest.fixture
def as_service(monkeypatch):
    async def _resolve(_request):
        return OperatorIdentity(role="admin", actor="service:internal-token")

    monkeypatch.setattr("backend.app.api.deps.resolve_operator_role", _resolve)


@pytest.fixture
def as_viewer(monkeypatch):
    async def _resolve(_request):
        return OperatorIdentity(role="viewer", actor="user:viewer-1")

    monkeypatch.setattr("backend.app.api.deps.resolve_operator_role", _resolve)


@pytest.mark.asyncio
async def test_observability_guard_allows_service_read_in_audit(as_service):
    # 只读放宽保留
    await observability.require_admin_operator(_request("GET", "/observability/x"))


@pytest.mark.asyncio
async def test_observability_guard_denies_service_write_in_audit(as_service):
    with pytest.raises(HTTPException) as exc:
        await observability.require_admin_operator(
            _request("POST", "/observability/x")
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_task_retry_rejects_service_in_audit(as_service):
    """任务中心重试仍必须拒绝服务凭据（audit 不是后门）。"""
    with pytest.raises(HTTPException) as exc:
        await admin_tasks.admin_retry_task(
            "task-1", admin_tasks.AdminOpRequest(confirm=True), _request(
                "POST", "/api/admin/tasks/task-1/retry"
            )
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_task_revoke_rejects_viewer_in_audit(as_viewer):
    """非管理员用户在 audit 下也不能撤销任务。"""
    with pytest.raises(HTTPException) as exc:
        await admin_tasks.admin_revoke_task(
            "task-1", admin_tasks.AdminOpRequest(confirm=True), _request(
                "POST", "/api/admin/tasks/task-1/revoke"
            )
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_task_reexecute_rejects_service_in_audit(as_service):
    """克隆重执行同属写操作，audit 下仍拒绝服务凭据。"""
    with pytest.raises(HTTPException) as exc:
        await admin_tasks.admin_reexecute_task(
            "task-1", admin_tasks.AdminOpRequest(confirm=True), _request(
                "POST", "/api/admin/tasks/task-1/reexecute"
            )
        )
    assert exc.value.status_code == 403
