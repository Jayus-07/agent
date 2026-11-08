"""登录面 P0 加固回归（2026-11-09）。

覆盖：
- 请求体非法 JSON → 400（不再落 500 兜底）；
- 新账号标识 11 位数字、密码 8–20 且含字母+数字的统一策略。
"""
from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.app.api.routes import auth_local
from backend.app.exceptions import invalid_json_exception_handler


def _client() -> TestClient:
    app = FastAPI()
    app.add_exception_handler(json.JSONDecodeError, invalid_json_exception_handler)

    @app.post("/echo")
    async def echo(request: Request):
        return await request.json()

    return TestClient(app, raise_server_exceptions=False)


def test_malformed_json_returns_400_not_500():
    resp = _client().post(
        "/echo", content=b"not-json{", headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "InvalidJSON"
    assert body["code"] == 400
    # 不回显请求体/堆栈
    assert "Traceback" not in resp.text
    assert "not-json" not in resp.text


def test_valid_json_still_passes():
    resp = _client().post(
        "/echo", json={"ok": True}, headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_account_must_be_11_digits():
    assert auth_local._ACCOUNT_RE.fullmatch("13800138000")
    assert not auth_local._ACCOUNT_RE.fullmatch("admin")
    assert not auth_local._ACCOUNT_RE.fullmatch("1380013800")
    assert not auth_local._ACCOUNT_RE.fullmatch("138001380001")


def test_password_policy_requires_letters_and_digits():
    assert auth_local._password_policy_error("abcd1234") is None
    assert auth_local._password_policy_error("12345678")  # 纯数字
    assert auth_local._password_policy_error("abcdefgh")  # 纯字母
    assert auth_local._password_policy_error("abc123")    # 太短
    assert auth_local._password_policy_error("a1" * 11)   # 太长


def test_portal_admission_rules():
    viewer = {"role": "viewer", "cs_role": None}
    admin = {"role": "admin", "cs_role": None}
    super_admin = {"role": "super_admin", "cs_role": None}
    agent = {"role": "viewer", "cs_role": "agent"}
    supervisor = {"role": "viewer", "cs_role": "supervisor"}

    # web 任意账号
    assert auth_local._portal_admission_error("web", viewer) is None
    # 管理端仅 admin/super_admin
    assert auth_local._portal_admission_error("admin", viewer)
    assert auth_local._portal_admission_error("admin", admin) is None
    assert auth_local._portal_admission_error("admin", super_admin) is None
    # 客服端：admin/super_admin 或绑定坐席
    assert auth_local._portal_admission_error("cs", viewer)
    assert auth_local._portal_admission_error("cs", admin) is None
    assert auth_local._portal_admission_error("cs", super_admin) is None
    assert auth_local._portal_admission_error("cs", agent) is None
    assert auth_local._portal_admission_error("cs", supervisor) is None
