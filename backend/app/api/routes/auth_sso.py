"""企业扫码登录：外部平台只确认身份，本地账号与 RBAC 仍是权限事实源。"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import Literal
from urllib.parse import quote, urlencode, urlsplit, parse_qsl

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app.api.deps import (
    OperatorIdentity,
    is_platform_admin,
    require_admin_user,
)
from backend.app.api.routes import auth_local
from backend.security.events import record_security_event
from backend.shared.crypto import (
    SecretCryptoError,
    decrypt_secret,
    encrypt_secret,
)
from backend.shared.logger import logger

public_router = APIRouter(prefix="/auth/sso", tags=["扫码登录"])
admin_router = APIRouter(prefix="/sys/sso", tags=["扫码登录管理"])

Provider = Literal["feishu", "wecom", "dingtalk"]
_PROVIDER_NAMES = {"feishu": "飞书", "wecom": "企业微信", "dingtalk": "钉钉"}
_STATE_TTL_SECONDS = 300
_STATE_KEY_PREFIX = "auth:sso:state:"


class StartLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    redirect_to: str = Field(default="/agent", max_length=2048)
    device_id: str = Field(default="", max_length=64)
    browser_nonce: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class CallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=1024)
    state: str = Field(min_length=20, max_length=256)
    browser_nonce: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class ProviderConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=255)
    client_secret: str | None = Field(default=None, max_length=4096)
    external_org_id: str = Field(min_length=1, max_length=255)
    redirect_uri: str = Field(min_length=1, max_length=2048)
    agent_id: str | None = Field(default=None, max_length=128)
    enabled: bool = True


class IdentityBindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Provider
    external_org_id: str = Field(min_length=1, max_length=255)
    external_subject_id: str = Field(min_length=1, max_length=255)
    local_user_id: int = Field(gt=0)


class SsoProviderError(RuntimeError):
    """外部身份平台拒绝或返回不完整身份。"""


def _state_key(state: str) -> str:
    digest = hashlib.sha256(state.encode("utf-8")).hexdigest()
    return f"{_STATE_KEY_PREFIX}{digest}"


def _safe_redirect_path(value: str) -> bool:
    return (
        value.startswith("/")
        and not value.startswith("//")
        and "\\" not in value
        and "\r" not in value
        and "\n" not in value
    )


def _validate_redirect_uri(provider: str, value: str) -> None:
    parsed = urlsplit(value)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    local_http = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        parsed.scheme != "https" and not (parsed.scheme == "http" and local_http)
    ):
        raise HTTPException(400, "回调地址必须使用 HTTPS")
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.path != "/login/sso/callback"
        or query != [("provider", provider)]
    ):
        raise HTTPException(
            400, "回调地址必须是 /login/sso/callback?provider=<provider>"
        )


def _authorization_url(
    provider: str,
    config: dict,
    state: str,
    code_verifier: str,
) -> str:
    redirect_uri = config["redirect_uri"]
    if provider == "feishu":
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        params = {
            "client_id": config["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "contact:user.base:readonly",
            "state": state,
            "target_tenant": config["external_org_id"],
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return "https://accounts.feishu.cn/open-apis/authen/v1/authorize?" + urlencode(
            params, quote_via=quote
        )
    if provider == "wecom":
        params = {
            "appid": config["client_id"],
            "agentid": config["agent_id"],
            "redirect_uri": redirect_uri,
            "state": state,
        }
        return "https://open.work.weixin.qq.com/wwopen/sso/qrConnect?" + urlencode(
            params, quote_via=quote
        )
    params = {
        "client_id": config["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid corpid Contact.User.Read",
        "state": state,
        "prompt": "consent",
    }
    return "https://login.dingtalk.com/oauth2/auth?" + urlencode(
        params, quote_via=quote
    )


async def _request_provider_json(method: str, url: str, **kwargs) -> dict:
    """只访问代码中固定的服务端点，关闭重定向并限制出站等待时间。"""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(8.0, connect=3.0), follow_redirects=False
    ) as client:
        response = await client.request(method, url, **kwargs)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise SsoProviderError("身份平台返回格式错误")
    return payload


def _provider_secret(config: dict) -> str:
    return decrypt_secret(str(config["client_secret_cipher"]), strict=True)


async def _exchange_provider_identity(
    provider: str,
    config: dict,
    code: str,
    *,
    code_verifier: str = "",
) -> tuple[str, str]:
    """兑换一次性授权码，仅返回组织 ID 和平台内稳定用户 ID。"""
    secret = _provider_secret(config)
    if provider == "feishu":
        token = await _request_provider_json(
            "POST",
            "https://accounts.feishu.cn/oauth/v3/token",
            json={
                "grant_type": "authorization_code",
                "client_id": config["client_id"],
                "client_secret": secret,
                "code": code,
                "redirect_uri": config["redirect_uri"],
                "code_verifier": code_verifier,
            },
        )
        access_token = token.get("access_token")
        if token.get("error") or not access_token:
            raise SsoProviderError("飞书授权码兑换失败")
        user = await _request_provider_json(
            "GET",
            "https://open.feishu.cn/open-apis/authen/v1/user_info",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        data = user.get("data") if isinstance(user.get("data"), dict) else user
        if user.get("code", 0) != 0:
            raise SsoProviderError("飞书身份校验失败")
        org_id, subject_id = data.get("tenant_key"), data.get("open_id")
    elif provider == "wecom":
        token = await _request_provider_json(
            "GET",
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
            params={"corpid": config["client_id"], "corpsecret": secret},
        )
        access_token = token.get("access_token")
        if token.get("errcode", 0) != 0 or not access_token:
            raise SsoProviderError("企业微信应用凭证校验失败")
        user = await _request_provider_json(
            "GET",
            "https://qyapi.weixin.qq.com/cgi-bin/auth/getuserinfo",
            params={"access_token": access_token, "code": code},
        )
        if user.get("errcode", 0) != 0:
            raise SsoProviderError("企业微信授权码校验失败")
        org_id = user.get("CorpId") or config["client_id"]
        subject_id = user.get("UserId")
    else:
        token = await _request_provider_json(
            "POST",
            "https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
            json={
                "clientId": config["client_id"],
                "clientSecret": secret,
                "code": code,
                "grantType": "authorization_code",
            },
        )
        access_token = token.get("accessToken")
        org_id = token.get("corpId") or token.get("corp_id")
        if not access_token:
            raise SsoProviderError("钉钉授权码兑换失败")
        user = await _request_provider_json(
            "GET",
            "https://api.dingtalk.com/v1.0/contact/users/me",
            headers={"x-acs-dingtalk-access-token": access_token},
        )
        subject_id = user.get("openId") or user.get("open_id")

    if not isinstance(org_id, str) or not org_id:
        raise SsoProviderError("身份平台未返回企业标识")
    if not isinstance(subject_id, str) or not subject_id:
        raise SsoProviderError("身份平台未返回可用的成员标识")
    return org_id, subject_id


async def _provider_config(tenant_id: str, provider: str) -> dict | None:
    async with auth_local._db() as session:
        row = (await session.execute(text(
            "SELECT tenant_id, provider, client_id, client_secret_cipher, "
            "external_org_id, agent_id, redirect_uri, enabled "
            "FROM auth.sso_provider_configs "
            "WHERE tenant_id = :tenant_id AND provider = :provider"),
            {"tenant_id": tenant_id, "provider": provider})).mappings().first()
    return dict(row) if row is not None else None


async def _require_sso_admin(request: Request) -> tuple[OperatorIdentity, str]:
    operator = await require_admin_user(request)
    # require_admin_user 在 audit 模式会放宽部分只读请求；SSO 凭据和绑定
    # 属于认证控制面，额外要求真实 JWT 管理员身份，不能让服务凭据或审计旁路通过。
    if operator.kind != "user" or not is_platform_admin(operator.role):
        raise HTTPException(403, "仅本地平台管理员可管理扫码登录")
    tenant_id = auth_local._trusted_tenant_id(request)
    if tenant_id is None:
        raise HTTPException(401, "缺少可信租户身份")
    return operator, tenant_id


def _audit_admin_action(event: str, operator: OperatorIdentity, tenant_id: str,
                        detail: dict) -> None:
    try:
        record_security_event(
            "ADMIN_ACTION", category=event, detail=detail,
            user_id=operator.actor.removeprefix("user:") or None,
            tenant_id=tenant_id,
        )
    except Exception:
        logger.warning("[SSO] 管理操作审计写入失败: %s", event, exc_info=True)


@public_router.get("/providers")
async def list_enabled_providers(request: Request):
    tenant_id = auth_local._trusted_tenant_id(request)
    if tenant_id is None:
        return auth_local._fail("缺少可信租户身份", code=401)
    async with auth_local._db() as session:
        rows = (await session.execute(text(
            "SELECT provider FROM auth.sso_provider_configs "
            "WHERE tenant_id = :tenant_id AND enabled IS TRUE "
            "ORDER BY provider"), {"tenant_id": tenant_id})).mappings().all()
    return auth_local._result([
        {"provider": row["provider"], "name": _PROVIDER_NAMES[row["provider"]]}
        for row in rows if row["provider"] in _PROVIDER_NAMES
    ])


@public_router.post("/{provider}/start")
async def start_login(provider: Provider, body: StartLoginRequest,
                      request: Request):
    tenant_id = auth_local._trusted_tenant_id(request)
    if tenant_id is None:
        return auth_local._fail("缺少可信租户身份", code=401)
    if not _safe_redirect_path(body.redirect_to):
        return auth_local._fail("登录完成后的页面地址无效", code=400)
    config = await _provider_config(tenant_id, provider)
    if not config or not config["enabled"]:
        return auth_local._fail("该扫码登录方式尚未启用", code=404)
    try:
        _validate_redirect_uri(provider, config["redirect_uri"])
    except HTTPException:
        logger.error("[SSO] 数据库中的回调地址无效 provider=%s", provider)
        return auth_local._fail("扫码登录配置无效，请联系管理员", code=503)
    redis = auth_local._get_auth_store()
    if redis is None:
        return auth_local._fail("扫码登录暂不可用，请稍后再试", code=503)

    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(48)
    client_app = auth_local._resolve_client_app(request)
    state_value = json.dumps({
        "tenant_id": tenant_id,
        "provider": provider,
        "redirect_uri": config["redirect_uri"],
        "redirect_to": body.redirect_to,
        "device_id": (body.device_id or secrets.token_urlsafe(18))[:64],
        "client_app": client_app,
        "code_verifier": code_verifier,
        "browser_nonce_hash": hashlib.sha256(
            body.browser_nonce.encode("ascii")
        ).hexdigest(),
    }, separators=(",", ":"))
    try:
        saved = redis.set(
            _state_key(state), state_value, ex=_STATE_TTL_SECONDS, nx=True
        )
    except Exception:
        logger.warning("[SSO] 创建一次性状态失败", exc_info=True)
        return auth_local._fail("扫码登录暂不可用，请稍后再试", code=503)
    if not saved:
        return auth_local._fail("扫码状态创建失败，请重试", code=503)
    return auth_local._result({
        "authorizationUrl": _authorization_url(
            provider, config, state, code_verifier
        ),
        "expiresIn": _STATE_TTL_SECONDS,
    })


@public_router.post("/{provider}/callback")
async def complete_login(provider: Provider, body: CallbackRequest,
                         request: Request, response: Response):
    tenant_id = auth_local._trusted_tenant_id(request)
    if tenant_id is None:
        return auth_local._fail("缺少可信租户身份", code=401)
    redis = auth_local._get_auth_store()
    if redis is None:
        return auth_local._fail("扫码登录暂不可用，请稍后再试", code=503)
    try:
        raw_state = redis.getdel(_state_key(body.state))
    except Exception:
        logger.warning("[SSO] 消费一次性状态失败", exc_info=True)
        return auth_local._fail("扫码登录状态校验暂不可用，请重试", code=503)
    if isinstance(raw_state, bytes):
        raw_state = raw_state.decode("utf-8", errors="replace")
    try:
        state_data = json.loads(raw_state) if raw_state else None
    except (TypeError, json.JSONDecodeError):
        state_data = None
    if (
        not isinstance(state_data, dict)
        or state_data.get("provider") != provider
        or state_data.get("tenant_id") != tenant_id
    ):
        return auth_local._fail("扫码登录已过期或无效，请重新扫码", code=400)
    browser_nonce_hash = hashlib.sha256(
        body.browser_nonce.encode("ascii")
    ).hexdigest()
    expected_nonce_hash = state_data.get("browser_nonce_hash")
    if (
        not isinstance(expected_nonce_hash, str)
        or not secrets.compare_digest(expected_nonce_hash, browser_nonce_hash)
    ):
        return auth_local._fail("扫码登录请求与当前浏览器不匹配，请重新扫码", code=400)

    config = await _provider_config(tenant_id, provider)
    if not config or not config["enabled"]:
        return auth_local._fail("该扫码登录方式已停用", code=403)
    if state_data.get("redirect_uri") != config["redirect_uri"]:
        return auth_local._fail("扫码登录配置已更新，请重新扫码", code=400)
    try:
        org_id, subject_id = await _exchange_provider_identity(
            provider, config, body.code,
            code_verifier=str(state_data.get("code_verifier") or ""),
        )
    except SecretCryptoError as exc:
        logger.error("[SSO] 应用密钥无法解密 provider=%s reason=%s",
                     provider, type(exc).__name__)
        return auth_local._fail("扫码登录配置暂不可用，请联系管理员", code=503)
    except Exception as exc:
        logger.warning(
            "[SSO] 外部身份校验失败 provider=%s reason=%s",
            provider, type(exc).__name__,
        )
        return auth_local._fail("外部平台身份校验失败，请重新扫码", code=502)
    if org_id != config["external_org_id"]:
        return auth_local._fail("当前登录的企业与本平台绑定企业不一致", code=403)

    async with auth_local._db() as session:
        row = (await session.execute(text(
            "SELECT u.id, u.username, u.password_hash, u.real_name, u.dept, "
            "u.role, u.status, u.phone, u.tenant_id, u.must_change_password "
            "FROM auth.external_identities i "
            "JOIN auth.users u ON u.id = i.local_user_id "
            "WHERE i.tenant_id = :tenant_id AND i.provider = :provider "
            "AND i.external_org_id = :org_id "
            "AND i.external_subject_id = :subject_id "
            "AND u.tenant_id = i.tenant_id"),
            {"tenant_id": tenant_id, "provider": provider,
             "org_id": org_id, "subject_id": subject_id})).mappings().first()
        if row is not None:
            row = await auth_local._enrich_user_row(session, row, tenant_id)
    if row is None:
        return auth_local._fail("该企业账号尚未绑定本平台账号，请联系管理员", code=403)
    if row["status"] != 1:
        return auth_local._fail("本平台账号已停用，请联系管理员", code=403)
    client_app = str(state_data.get("client_app") or "web")
    portal_error = auth_local._portal_admission_error(client_app, row)
    if portal_error:
        return auth_local._fail(portal_error, code=403)
    if row.get("must_change_password"):
        return auth_local._fail(
            "请先使用本地账号完成首次密码修改，再使用扫码登录", code=403
        )

    login_data = await auth_local._issue_login_session(
        row, request, response,
        device_id=str(state_data.get("device_id") or "")[:64],
        client_app=client_app,
    )
    login_data["redirectTo"] = state_data.get("redirect_to", "/agent")
    return auth_local._result(login_data)


@admin_router.get("/providers")
async def get_provider_configs(request: Request):
    _, tenant_id = await _require_sso_admin(request)
    async with auth_local._db() as session:
        rows = (await session.execute(text(
            "SELECT provider, client_id, external_org_id, agent_id, "
            "redirect_uri, enabled, (client_secret_cipher <> '') AS has_secret "
            "FROM auth.sso_provider_configs WHERE tenant_id = :tenant_id "
            "ORDER BY provider"), {"tenant_id": tenant_id})).mappings().all()
    return auth_local._result([dict(row) for row in rows])


@admin_router.put("/providers/{provider}")
async def save_provider_config(provider: Provider, body: ProviderConfigRequest,
                               request: Request):
    operator, tenant_id = await _require_sso_admin(request)
    client_id = body.client_id.strip()
    org_id = body.external_org_id.strip()
    redirect_uri = body.redirect_uri.strip()
    agent_id = (body.agent_id or "").strip() or None
    if not client_id or not org_id:
        return auth_local._fail("应用 ID 和企业组织 ID 不能为空", code=400)
    try:
        _validate_redirect_uri(provider, redirect_uri)
    except HTTPException as exc:
        return auth_local._fail(str(exc.detail), code=exc.status_code)
    except ValueError:
        return auth_local._fail("回调地址格式无效", code=400)
    if provider == "wecom" and (client_id != org_id or not agent_id):
        return auth_local._fail(
            "企业微信需填写相同的 CorpID 和组织 ID，并提供 AgentId", code=400
        )
    if provider != "wecom" and agent_id:
        return auth_local._fail("只有企业微信需要 AgentId", code=400)

    existing = await _provider_config(tenant_id, provider)
    secret = (body.client_secret or "").strip()
    if not secret and not existing:
        return auth_local._fail("首次配置必须填写应用密钥", code=400)
    try:
        cipher = encrypt_secret(secret) if secret else existing["client_secret_cipher"]
    except Exception as exc:
        logger.warning("[SSO] 应用密钥加密配置不可用: %s", type(exc).__name__)
        return auth_local._fail("服务端密钥加密未配置，无法保存应用凭证", code=503)

    async with auth_local._db() as session:
        await session.execute(text(
            "INSERT INTO auth.sso_provider_configs "
            "(tenant_id, provider, client_id, client_secret_cipher, "
            "external_org_id, agent_id, redirect_uri, enabled) "
            "VALUES (:tenant_id, :provider, :client_id, :secret, :org_id, "
            ":agent_id, :redirect_uri, :enabled) "
            "ON CONFLICT (tenant_id, provider) DO UPDATE SET "
            "client_id = EXCLUDED.client_id, "
            "client_secret_cipher = EXCLUDED.client_secret_cipher, "
            "external_org_id = EXCLUDED.external_org_id, "
            "agent_id = EXCLUDED.agent_id, redirect_uri = EXCLUDED.redirect_uri, "
            "enabled = EXCLUDED.enabled, updated_at = now()"),
            {"tenant_id": tenant_id, "provider": provider,
             "client_id": client_id, "secret": cipher, "org_id": org_id,
             "agent_id": agent_id, "redirect_uri": redirect_uri,
             "enabled": body.enabled})
        await session.commit()
    _audit_admin_action(
        "SSO_PROVIDER_CONFIG_SAVED", operator, tenant_id,
        {"provider": provider, "enabled": body.enabled},
    )
    return auth_local._result({"provider": provider, "enabled": body.enabled})


@admin_router.get("/identities")
async def list_identity_bindings(request: Request):
    _, tenant_id = await _require_sso_admin(request)
    async with auth_local._db() as session:
        rows = (await session.execute(text(
            "SELECT i.id, i.provider, i.external_org_id, "
            "i.external_subject_id, i.local_user_id, u.username, u.real_name, "
            "i.created_at FROM auth.external_identities i "
            "JOIN auth.users u ON u.id = i.local_user_id "
            "WHERE i.tenant_id = :tenant_id ORDER BY i.created_at DESC"),
            {"tenant_id": tenant_id})).mappings().all()
    return auth_local._result([dict(row) for row in rows])


@admin_router.post("/identities")
async def bind_identity(body: IdentityBindingRequest, request: Request):
    operator, tenant_id = await _require_sso_admin(request)
    config = await _provider_config(tenant_id, body.provider)
    if not config:
        return auth_local._fail("请先配置该扫码登录平台", code=409)
    if body.external_org_id != config["external_org_id"]:
        return auth_local._fail("身份所属企业与当前登录配置不一致", code=400)

    async with auth_local._db() as session:
        user = (await session.execute(text(
            "SELECT id FROM auth.users WHERE id = :user_id "
            "AND tenant_id = :tenant_id AND status = 1"),
            {"user_id": body.local_user_id, "tenant_id": tenant_id})).first()
        if user is None:
            return auth_local._fail("只能绑定当前租户内启用的本地账号", code=404)
        try:
            result = await session.execute(text(
                "INSERT INTO auth.external_identities "
                "(tenant_id, provider, external_org_id, external_subject_id, "
                "local_user_id, created_by) "
                "VALUES (:tenant_id, :provider, :org_id, :subject_id, "
                ":local_user_id, :created_by) RETURNING id"),
                {"tenant_id": tenant_id, "provider": body.provider,
                 "org_id": body.external_org_id,
                 "subject_id": body.external_subject_id,
                 "local_user_id": body.local_user_id,
                 "created_by": operator.actor[:128]})
            mapping_id = result.scalar_one()
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return auth_local._fail("该外部身份或本地账号已绑定", code=409)
    _audit_admin_action(
        "SSO_IDENTITY_BOUND", operator, tenant_id,
        {"provider": body.provider, "local_user_id": body.local_user_id},
    )
    return auth_local._result({"id": mapping_id})


@admin_router.delete("/identities/{mapping_id}")
async def remove_identity_binding(mapping_id: int, request: Request):
    operator, tenant_id = await _require_sso_admin(request)
    async with auth_local._db() as session:
        result = await session.execute(text(
            "DELETE FROM auth.external_identities "
            "WHERE id = :id AND tenant_id = :tenant_id"),
            {"id": mapping_id, "tenant_id": tenant_id})
        if result.rowcount == 0:
            return auth_local._fail("绑定记录不存在", code=404)
        await session.commit()
    _audit_admin_action(
        "SSO_IDENTITY_UNBOUND", operator, tenant_id,
        {"mapping_id": mapping_id},
    )
    return auth_local._result({"id": mapping_id, "deleted": True})
