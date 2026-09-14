"""test_identity.py — P3 身份解析单一入口契约（backend/app/api/identity.py）

三模式语义：
  legacy  = 请求体 user_id 优先 + TRUST_USER_HEADER 头兜底（现网兼容，可伪造窗口仍在）
  header  = 网关权威：body 身份一律无视，缺头降级 guest
  strict  = header + 未认证 401

另覆盖 sql._resolve_user_id 的收敛适配（fail-closed 语义不回归）。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.app.api import identity as identity_mod
from backend.app.api.identity import require_identity, resolve_identity
from backend.app.api.routes import sql as sql_route


@pytest.fixture()
def client(monkeypatch):
    """挂一个最小 app：/whoami 返回 resolve_identity 结果，/strict 走 require_identity。"""
    app = FastAPI()

    @app.post("/whoami")
    async def whoami(request: Request):
        ident = resolve_identity(request, body_user_id=request.query_params.get("uid"))
        return {
            "user_id": ident.user_id, "user_name": ident.user_name,
            "department": ident.department, "auth_type": ident.auth_type,
            "source": ident.source,
        }

    @app.get("/strict")
    async def strict_route(request: Request):
        ident = require_identity(request)
        return {"user_id": ident.user_id}

    return TestClient(app, raise_server_exceptions=True)


def _set_mode(monkeypatch, mode: str):
    monkeypatch.setattr(identity_mod, "identity_source", lambda: mode)
    # resolve_identity 内部经 identity_source() 读取；config.auth 的常量同步改
    from backend.config import auth as auth_cfg
    monkeypatch.setattr(auth_cfg, "IDENTITY_SOURCE", mode)


# ── legacy：现网兼容行为 ──

def test_legacy_body_user_id_wins(client, monkeypatch):
    _set_mode(monkeypatch, "legacy")
    monkeypatch.setattr("backend.config.TRUST_USER_HEADER", False)
    r = client.post("/whoami?uid=15")
    assert r.json()["source"] == "body"
    assert r.json()["user_id"] == "15"


def test_legacy_no_body_no_trust_header_is_guest(client, monkeypatch):
    _set_mode(monkeypatch, "legacy")
    monkeypatch.setattr("backend.config.TRUST_USER_HEADER", False)
    r = client.post("/whoami", headers={"X-User-Id": "15"})  # 开关关：头也不认
    assert r.json() == {"user_id": "", "user_name": "", "department": "",
                        "auth_type": "guest", "source": "guest"}


def test_legacy_falls_back_to_gateway_header(client, monkeypatch):
    _set_mode(monkeypatch, "legacy")
    monkeypatch.setattr("backend.config.TRUST_USER_HEADER", True)
    r = client.post("/whoami", headers={"X-User-Id": "15", "X-User-Name": "Mint",
                                        "X-User-Dept": "rd", "X-Auth-Type": "jwt"})
    body = r.json()
    assert body["user_id"] == "15" and body["auth_type"] == "jwt"


# ── header：网关权威 ──

def test_header_mode_ignores_body_identity(client, monkeypatch):
    _set_mode(monkeypatch, "header")
    r = client.post("/whoami?uid=999")  # body 身份必须被无视
    assert r.json()["source"] == "guest"


def test_header_mode_full_fields(client, monkeypatch):
    _set_mode(monkeypatch, "header")
    r = client.post("/whoami", headers={"X-User-Id": "15", "X-User-Name": "Mint",
                                        "X-User-Dept": "rd"})
    assert r.json() == {"user_id": "15", "user_name": "Mint", "department": "rd",
                        "auth_type": "jwt", "source": "header"}


# ── strict：未认证 401 ──

def test_strict_requires_identity_header(client, monkeypatch):
    _set_mode(monkeypatch, "strict")
    resp = client.get("/strict")
    assert resp.status_code == 401


def test_strict_passes_with_header(client, monkeypatch):
    _set_mode(monkeypatch, "strict")
    resp = client.get("/strict", headers={"X-User-Id": "15"})
    assert resp.json() == {"user_id": "15"}


# ── sql._resolve_user_id 收敛适配（fail-closed 不回归）──

class _FakeRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


def test_sql_header_mode_reads_gateway_uid(monkeypatch):
    _set_mode(monkeypatch, "header")
    req = _FakeRequest({"X-User-Id": "15"})
    assert sql_route._resolve_user_id(req) == 15  # type: ignore[arg-type]


def test_sql_guest_stays_none(monkeypatch):
    _set_mode(monkeypatch, "header")
    assert sql_route._resolve_user_id(_FakeRequest()) is None  # type: ignore[arg-type]


def test_sql_non_int_uid_ignored(monkeypatch):
    _set_mode(monkeypatch, "header")
    req = _FakeRequest({"X-User-Id": "not-an-int"})
    assert sql_route._resolve_user_id(req) is None  # type: ignore[arg-type]
