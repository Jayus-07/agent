"""tests/selection_funnel/test_api_import.py — 导入通道 API 冒烟

TestClient 只挂本域 router（不拉全 app），配合 conftest 的
isolated_import_store 内存替身，单测绝不打真实 PG（agent_business）。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from backend.app.api.deps import (
        OperatorIdentity, require_editor_user, resolve_operator_role,
    )
    from backend.app.api.routes.selection_funnel import router
    app = FastAPI()
    app.include_router(router)
    # 2026-09-24 STOP A：路由挂运营角色门禁；本文件只测导入契约，
    # 门禁行为由 tests/api/test_selection_routes_authz.py 覆盖
    app.dependency_overrides[resolve_operator_role] = lambda: OperatorIdentity(
        role="editor", actor="user:test")
    # 写接口通过独立依赖校验，业务测试显式提供已授权用户。
    app.dependency_overrides[require_editor_user] = lambda: OperatorIdentity(
        role="editor", actor="user:test")
    return TestClient(app)


TSV = "商品标题\t价格\t评分\t评价人数\t平台\t类目\n冻干鸡肉 500g\t149\t4.8\t1.5万\t淘宝\t宠物零食\n"

_IDENTITY = {"X-Auth-Type": "jwt", "X-User-Id": "u-editor",
             "X-User-Roles": "editor"}
TENANT_A = {**_IDENTITY, "X-Tenant-Id": "tenant-a"}
TENANT_B = {**_IDENTITY, "X-Tenant-Id": "tenant-b"}


def test_import_text_and_list(client):
    resp = client.post("/selection-funnel/import/text", json={
        "content": TSV, "category": "", "platform": ""}, headers=TENANT_A)
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1 and body["batch_id"].startswith("imp-")

    listed = client.get("/selection-funnel/import/candidates",
                        params={"category": "宠物零食"},
                        headers=TENANT_A).json()
    assert listed["count"] == 1
    assert listed["items"][0]["title"] == "冻干鸡肉 500g"


def test_import_text_bad_payload_400(client):
    resp = client.post("/selection-funnel/import/text",
                       json={"content": "这不是表格"}, headers=TENANT_A)
    assert resp.status_code == 400
    assert "导入失败" in resp.json()["detail"]


def test_clear_batch_404(client):
    assert client.delete(
        "/selection-funnel/import/batch/imp-none", headers=TENANT_A
    ).status_code == 404


def test_import_is_tenant_scoped(client):
    """2026-11-08 审计 T4d：导入/列表/删除按租户隔离。"""
    created = client.post("/selection-funnel/import/text", json={
        "content": TSV, "kind": "products"}, headers=TENANT_A)
    assert created.status_code == 200
    batch_id = created.json()["batch_id"]

    # 他租列表看不到
    other = client.get("/selection-funnel/import/candidates",
                       headers=TENANT_B).json()
    assert other["count"] == 0
    # 他租删除 → 404，本租数据不受影响
    denied = client.delete(
        f"/selection-funnel/import/batch/{batch_id}", headers=TENANT_B)
    assert denied.status_code == 404
    assert client.get("/selection-funnel/import/candidates",
                      headers=TENANT_A).json()["count"] == 1
    # 本租删除成功
    ok = client.delete(
        f"/selection-funnel/import/batch/{batch_id}", headers=TENANT_A)
    assert ok.status_code == 200 and ok.json()["removed"] == 1


def test_missing_tenant_fails_closed(client):
    resp = client.get("/selection-funnel/import/candidates")
    assert resp.status_code == 403
