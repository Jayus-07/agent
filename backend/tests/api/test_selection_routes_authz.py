"""tests/api/test_selection_routes_authz.py — 选品域路由运营门禁回归（2026-09-24 STOP A）

P0 修复背景：selection_decision / selection_funnel 两个路由此前无任何身份门禁
（网关 JWT 后任意已认证用户可读/写全部任务与导入池，无角色档位、无操作者留痕）。
收口动作：两路由统一挂 deps.resolve_operator_role（JWT 用户取平台角色，
服务凭据走内部令牌映射 admin，与 prompts/model_prices 同档）。

本文件只测门禁三态，业务契约在 tests/selection_decision/ 与 tests/selection_funnel/：
- 无凭据 → 401（require_internal_token 拒绝，打桩保证确定性）
- JWT 用户（平台角色 viewer）→ 放行
- 服务凭据（X-Internal-Token）→ 放行且映射 admin（create 留痕 actor）
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import backend.app.api.routes.selection_decision as decision_mod
import backend.app.api.routes.selection_funnel as funnel_mod
from backend.selection_funnel import import_pool as import_pool_mod


def _build_app(router) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _deny_internal_token(monkeypatch):
    async def _deny(request):
        raise HTTPException(status_code=401, detail="缺少内部令牌")

    monkeypatch.setattr("backend.app.api.deps.require_internal_token", _deny)


_JWT_HEADERS = {"X-User-Id": "15", "X-Auth-Type": "jwt", "X-User-Roles": "viewer"}


class TestSelectionDecisionAuthz:
    def test_no_credentials_rejected(self, monkeypatch):
        _deny_internal_token(monkeypatch)
        client = _build_app(decision_mod.router)
        resp = client.get("/selection-decision/tasks")
        assert resp.status_code == 401

    def test_jwt_viewer_allowed(self, monkeypatch):
        class _StubStore:
            def list(self, page=1, page_size=20):
                return []

        monkeypatch.setattr(
            decision_mod, "get_selection_decision_store", lambda: _StubStore())
        client = _build_app(decision_mod.router)
        resp = client.get("/selection-decision/tasks", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == {"tasks": []}

    def test_internal_token_allowed(self, monkeypatch):
        monkeypatch.setattr("backend.config.messaging.AI_INTERNAL_TOKEN", "tok-1")

        class _StubStore:
            def list(self, page=1, page_size=20):
                return []

        monkeypatch.setattr(
            decision_mod, "get_selection_decision_store", lambda: _StubStore())
        client = _build_app(decision_mod.router)
        resp = client.get("/selection-decision/tasks",
                          headers={"X-Internal-Token": "tok-1"})
        # 服务凭据通道放行（映射 admin，映射语义由 test_operator_role_rbac 覆盖）
        assert resp.status_code == 200
        assert resp.json() == {"tasks": []}


class TestSelectionFunnelAuthz:
    def test_no_credentials_rejected(self, monkeypatch):
        _deny_internal_token(monkeypatch)
        client = _build_app(funnel_mod.router)
        resp = client.get("/selection-funnel/import/candidates")
        assert resp.status_code == 401

    def test_jwt_viewer_allowed(self, monkeypatch):
        class _StubImportStore:
            def list_candidates(self, category="", platform=""):
                return []

        monkeypatch.setattr(import_pool_mod, "get_import_store",
                            lambda: _StubImportStore())
        client = _build_app(funnel_mod.router)
        resp = client.get("/selection-funnel/import/candidates", headers=_JWT_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == {"count": 0, "items": []}
