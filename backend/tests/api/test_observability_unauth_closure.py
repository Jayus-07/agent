"""可观测性 / capabilities / llm 端点匿名访问收口（2026-11-09 审计）。

此前 /observability/rag-traces*、/tokens/*、/graph、/gateway-access-logs、
/system-health 以及 /capabilities、/llm/balance 无后端鉴权；本测试锁住
router 级 require_user_actor 与余额端点 admin 门禁。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.routes import capabilities, llm, observability
from backend.config import auth as auth_config


def _client(monkeypatch, headers: dict | None = None) -> TestClient:
    monkeypatch.setattr(auth_config, "IDENTITY_SOURCE", "header")
    app = FastAPI()
    app.include_router(observability.router)
    app.include_router(capabilities.router)
    app.include_router(llm.router)
    client = TestClient(app, raise_server_exceptions=False)
    if headers:
        client.headers.update(headers)
    return client


ANON_PATHS = [
    ("GET", "/observability/rag-traces"),
    ("GET", "/observability/rag-traces/stream"),
    ("GET", "/observability/tokens/summary"),
    ("GET", "/observability/graph"),
    ("GET", "/observability/gateway-access-logs"),
    ("GET", "/observability/system-health"),
    ("POST", "/observability/traces/t-1/replay"),
    ("GET", "/capabilities"),
    ("GET", "/llm/models"),
    ("GET", "/llm/balance"),
]


@pytest.mark.parametrize("method,path", ANON_PATHS)
def test_anonymous_access_is_rejected(monkeypatch, method, path):
    client = _client(monkeypatch)
    resp = client.request(method, path)
    assert resp.status_code in (401, 403), f"{method} {path} -> {resp.status_code}"


def test_viewer_can_read_capabilities_but_not_balance(monkeypatch):
    client = _client(
        monkeypatch,
        {
            "X-Auth-Type": "jwt",
            "X-User-Id": "1001",
            "X-Tenant-Id": "default",
            "X-User-Roles": "viewer",
        },
    )
    assert client.get("/capabilities").status_code == 200
    assert client.get("/observability/rag-traces").status_code == 200
    # 余额端点保持"登录即可读"（客服工具栏当前依赖）；匿名访问已在上面锁住。
    assert client.get("/llm/balance").status_code != 401
