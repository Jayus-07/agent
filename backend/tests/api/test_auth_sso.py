"""扫码登录安全边界：一次性 state、组织校验和本地权限复用。"""

from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.app.api.deps import OperatorIdentity
from backend.app.api.routes import auth_local, auth_sso

_BROWSER_NONCE = "a" * 64


class _FakeRedis:
    def __init__(self):
        self.values = {}

    def set(self, key, value, *, ex, nx):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    def getdel(self, key):
        return self.values.pop(key, None)


class _Mappings:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class _Result:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return _Mappings(self.row)


class _FakeSession:
    def __init__(self, row):
        self.row = row

    async def execute(self, _statement, _params):
        return _Result(self.row)


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(auth_sso.public_router)
    return app


def test_authorization_urls_use_provider_specific_identity_scopes():
    config = {
        "client_id": "client-1",
        "external_org_id": "org-1",
        "agent_id": "agent-2",
        "redirect_uri": "https://assistant.example/login/sso/callback?provider=feishu",
    }
    feishu_url = auth_sso._authorization_url(
        "feishu", config, "state-a", "a" * 48
    )
    feishu = parse_qs(urlsplit(feishu_url).query)
    assert "accounts.feishu.cn/open-apis/authen/v1/authorize" in feishu_url
    assert feishu["state"] == ["state-a"]
    assert feishu["target_tenant"] == ["org-1"]
    assert feishu["code_challenge_method"] == ["S256"]
    assert feishu["scope"] == ["contact:user.base:readonly"]

    config["redirect_uri"] = "https://assistant.example/login/sso/callback?provider=wecom"
    wecom_url = auth_sso._authorization_url("wecom", config, "state-b", "verifier")
    wecom = parse_qs(urlsplit(wecom_url).query)
    assert wecom["appid"] == ["client-1"]
    assert wecom["agentid"] == ["agent-2"]

    config["redirect_uri"] = "https://assistant.example/login/sso/callback?provider=dingtalk"
    ding_url = auth_sso._authorization_url(
        "dingtalk", config, "state-c", "verifier"
    )
    ding = parse_qs(urlsplit(ding_url).query)
    assert ding["scope"] == ["openid corpid Contact.User.Read"]
    assert ding["prompt"] == ["consent"]


def test_redirect_uri_is_limited_to_provider_callback():
    auth_sso._validate_redirect_uri(
        "feishu", "https://assistant.example/login/sso/callback?provider=feishu"
    )
    for uri in (
        "https://evil.example/callback?provider=feishu",
        "https://assistant.example/login/sso/callback?provider=wecom",
        "//evil.example/login/sso/callback?provider=feishu",
    ):
        with pytest.raises(HTTPException):
            auth_sso._validate_redirect_uri("feishu", uri)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "responses", "expected"),
    [
        (
            "feishu",
            [
                {"access_token": "token"},
                {"code": 0, "data": {"tenant_key": "org-a", "open_id": "user-a"}},
            ],
            ("org-a", "user-a"),
        ),
        (
            "wecom",
            [
                {"errcode": 0, "access_token": "token"},
                {"errcode": 0, "UserId": "user-b"},
            ],
            ("org-b", "user-b"),
        ),
        (
            "dingtalk",
            [
                {"accessToken": "token", "corpId": "org-c"},
                {"openId": "user-c"},
            ],
            ("org-c", "user-c"),
        ),
    ],
)
async def test_provider_exchange_returns_only_organization_and_subject(
    monkeypatch, provider, responses, expected
):
    calls = []

    async def request_json(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return responses[len(calls) - 1]

    monkeypatch.setattr(auth_sso, "_provider_secret", lambda _config: "secret")
    monkeypatch.setattr(auth_sso, "_request_provider_json", request_json)
    config = {
        "client_id": "org-b" if provider == "wecom" else "client-id",
        "client_secret_cipher": "cipher",
        "external_org_id": expected[0],
        "redirect_uri": f"https://assistant.example/login/sso/callback?provider={provider}",
    }

    identity = await auth_sso._exchange_provider_identity(
        provider, config, "one-time-code", code_verifier="verifier"
    )

    assert identity == expected
    assert len(calls) == 2
    if provider == "dingtalk":
        assert calls[1][2]["headers"]["x-acs-dingtalk-access-token"] == "token"
    if provider == "feishu":
        assert calls[0][2]["json"]["code_verifier"] == "verifier"


def test_start_creates_tenant_scoped_one_time_state(monkeypatch):
    redis = _FakeRedis()

    async def provider_config(tenant_id, _provider):
        assert tenant_id == "tenant-a"
        return {
            "client_id": "app-1",
            "external_org_id": "tenant-a",
            "agent_id": None,
            "redirect_uri": "https://assistant.example/login/sso/callback?provider=feishu",
            "enabled": True,
        }

    monkeypatch.setattr(auth_sso, "_provider_config", provider_config)
    monkeypatch.setattr(auth_local, "_get_auth_store", lambda: redis)
    app = _app()
    with TestClient(app) as client:
        response = client.post(
            "/auth/sso/feishu/start",
            headers={"X-Tenant-Id": "tenant-a"},
            json={
                "redirect_to": "/agent?tab=main",
                "device_id": "device-a",
                "browser_nonce": _BROWSER_NONCE,
            },
        )

    assert response.status_code == 200
    data = response.json()["data"]
    params = parse_qs(urlsplit(data["authorizationUrl"]).query)
    assert params["target_tenant"] == ["tenant-a"]
    state = params["state"][0]
    stored = json.loads(redis.values[auth_sso._state_key(state)])
    assert stored["tenant_id"] == "tenant-a"
    assert stored["provider"] == "feishu"
    assert stored["device_id"] == "device-a"
    assert stored["redirect_to"] == "/agent?tab=main"
    assert stored["browser_nonce_hash"] == hashlib.sha256(
        _BROWSER_NONCE.encode("ascii")
    ).hexdigest()
    assert _BROWSER_NONCE not in json.dumps(stored)
    assert "state-a" not in redis.values


def test_callback_rejects_identity_from_another_organization(monkeypatch):
    state = "x" * 40
    redis = _FakeRedis()
    redis.set(
        auth_sso._state_key(state),
        json.dumps({
            "tenant_id": "tenant-a", "provider": "feishu",
            "redirect_uri": "https://assistant.example/login/sso/callback?provider=feishu",
            "browser_nonce_hash": hashlib.sha256(
                _BROWSER_NONCE.encode("ascii")
            ).hexdigest(),
        }),
        ex=300, nx=True,
    )

    async def provider_config(_tenant_id, _provider):
        return {
            "enabled": True,
            "external_org_id": "org-a",
            "redirect_uri": "https://assistant.example/login/sso/callback?provider=feishu",
        }

    async def exchange(*_args, **_kwargs):
        return "org-b", "user-a"

    @asynccontextmanager
    async def unexpected_db_access():
        raise AssertionError("组织标识不匹配时不得查询本地账号")
        yield None

    monkeypatch.setattr(auth_sso, "_provider_config", provider_config)
    monkeypatch.setattr(auth_sso, "_exchange_provider_identity", exchange)
    monkeypatch.setattr(auth_local, "_get_auth_store", lambda: redis)
    monkeypatch.setattr(auth_local, "_db", unexpected_db_access)

    with TestClient(_app()) as client:
        response = client.post(
            "/auth/sso/feishu/callback",
            headers={"X-Tenant-Id": "tenant-a"},
            json={"code": "code", "state": state, "browser_nonce": _BROWSER_NONCE},
        )

    assert response.status_code == 403
    assert "企业" in response.json()["message"]


@pytest.mark.parametrize(
    ("mapped_row", "expected_status", "expected_login_calls"),
    [
        (
            {
                "id": 42, "username": "local-user", "password_hash": "hash",
                "real_name": "本地姓名", "dept": "总部", "role": "admin",
                "status": 1, "phone": "13800000000", "tenant_id": "tenant-a",
                "must_change_password": False, "cs_role": "supervisor",
            },
            200, 1,
        ),
        (None, 403, 0),
    ],
)
def test_callback_uses_bound_local_identity_and_state_once(
    monkeypatch, mapped_row, expected_status, expected_login_calls
):
    state = "s" * 40
    redis = _FakeRedis()
    redis.set(
        auth_sso._state_key(state),
        json.dumps({
            "tenant_id": "tenant-a", "provider": "feishu",
            "redirect_uri": "https://assistant.example/login/sso/callback?provider=feishu",
            "redirect_to": "/agent?tab=reports", "device_id": "device-a",
            "client_app": "web", "code_verifier": "verifier",
            "browser_nonce_hash": hashlib.sha256(
                _BROWSER_NONCE.encode("ascii")
            ).hexdigest(),
        }),
        ex=300, nx=True,
    )
    login_calls = []

    async def provider_config(_tenant_id, _provider):
        return {
            "enabled": True,
            "external_org_id": "org-a",
            "client_secret_cipher": "encrypted",
            "redirect_uri": "https://assistant.example/login/sso/callback?provider=feishu",
        }

    async def exchange(_provider, _config, _code, *, code_verifier=""):
        assert code_verifier == "verifier"
        return "org-a", "open-user-1"

    async def enrich(_session, row, _tenant_id):
        return dict(row)

    async def issue(row, _request, _response, *, device_id, client_app):
        login_calls.append((row, device_id, client_app))
        return {
            "token": "local-jwt",
            "tokenType": "Bearer",
            "userInfo": auth_local._user_info(row),
        }

    @asynccontextmanager
    async def fake_db():
        yield _FakeSession(mapped_row)

    monkeypatch.setattr(auth_sso, "_provider_config", provider_config)
    monkeypatch.setattr(auth_sso, "_exchange_provider_identity", exchange)
    monkeypatch.setattr(auth_local, "_get_auth_store", lambda: redis)
    monkeypatch.setattr(auth_local, "_db", fake_db)
    monkeypatch.setattr(auth_local, "_enrich_user_row", enrich)
    monkeypatch.setattr(auth_local, "_portal_admission_error", lambda *_args: None)
    monkeypatch.setattr(auth_local, "_issue_login_session", issue)

    with TestClient(_app()) as client:
        response = client.post(
            "/auth/sso/feishu/callback",
            headers={"X-Tenant-Id": "tenant-a"},
            json={
                "code": "one-time-code",
                "state": state,
                "browser_nonce": _BROWSER_NONCE,
            },
        )
        second = client.post(
            "/auth/sso/feishu/callback",
            headers={"X-Tenant-Id": "tenant-a"},
            json={
                "code": "one-time-code",
                "state": state,
                "browser_nonce": _BROWSER_NONCE,
            },
        )

    assert response.status_code == expected_status
    assert len(login_calls) == expected_login_calls
    assert second.status_code == 400
    if expected_status == 200:
        data = response.json()["data"]
        assert data["userInfo"]["platformRole"] == "admin"
        assert data["userInfo"]["csRole"] == "supervisor"
        assert data["redirectTo"] == "/agent?tab=reports"
        assert login_calls[0][1:] == ("device-a", "web")


def test_callback_rejects_state_from_another_browser(monkeypatch):
    state = "m" * 40
    redis = _FakeRedis()
    redis.set(
        auth_sso._state_key(state),
        json.dumps({
            "tenant_id": "tenant-a", "provider": "feishu",
            "browser_nonce_hash": hashlib.sha256(
                _BROWSER_NONCE.encode("ascii")
            ).hexdigest(),
        }),
        ex=300, nx=True,
    )
    exchange_calls = []

    async def unexpected_exchange(*_args, **_kwargs):
        exchange_calls.append(True)
        raise AssertionError("浏览器校验失败时不得兑换外部授权码")

    monkeypatch.setattr(auth_local, "_get_auth_store", lambda: redis)
    monkeypatch.setattr(auth_sso, "_exchange_provider_identity", unexpected_exchange)

    with TestClient(_app()) as client:
        response = client.post(
            "/auth/sso/feishu/callback",
            headers={"X-Tenant-Id": "tenant-a"},
            json={"code": "code", "state": state, "browser_nonce": "b" * 64},
        )

    assert response.status_code == 400
    assert "浏览器" in response.json()["message"]
    assert exchange_calls == []


@pytest.mark.anyio
async def test_provider_config_rejects_whitespace_identifiers(monkeypatch):
    async def admin(_request):
        return OperatorIdentity(role="admin", actor="user:1"), "tenant-a"

    monkeypatch.setattr(auth_sso, "_require_sso_admin", admin)
    request = Request({
        "type": "http", "method": "PUT", "path": "/sys/sso/providers/feishu",
        "headers": [],
    })
    body = auth_sso.ProviderConfigRequest(
        client_id=" ", client_secret="secret", external_org_id=" ",
        redirect_uri="https://assistant.example/login/sso/callback?provider=feishu",
    )

    response = await auth_sso.save_provider_config("feishu", body, request)

    assert response.status_code == 400


@pytest.mark.anyio
async def test_service_identity_cannot_manage_sso_even_in_read_only_mode(monkeypatch):
    async def service_admin(_request):
        return OperatorIdentity(role="admin", actor="service:internal-token")

    monkeypatch.setattr(auth_sso, "require_admin_user", service_admin)
    from starlette.requests import Request

    request = Request({"type": "http", "method": "GET", "path": "/sys/sso/providers", "headers": []})
    with pytest.raises(HTTPException) as error:
        await auth_sso._require_sso_admin(request)
    assert error.value.status_code == 403
