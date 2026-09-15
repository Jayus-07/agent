"""security/local_jwt.py — py 自建认证的 JWT 签发/验签与口令哈希。

背景（2026-09-15 项目拆分）：Java auth-service 退役后，py 侧成为 JWT 的
**唯一签发方**（原签发方是 Java auth-service，HS 系 + issuer hongmeng-oa）。
新 issuer = agent-platform；验签方 = APISIX gateway-auth 插件（黑名单只读），
logout 黑名单写入方 = 本模块（签发方=写入方=py，闭环自洽）。

实现约束：
- 纯标准库（hmac/hashlib），不引入 PyJWT 等新依赖——签发与验签都在本仓内闭环，
  格式为标准 JWT（HS512），APISIX 侧 lua-resty-jwt 按密钥长度自动选 HS512 兼容。
- 密码哈希：pbkdf2_hmac-SHA256（OWASP 建议迭代量级），格式
  pbkdf2_sha256$<iter>$<salt_hex>$<hash_hex>。
- 密钥来源：env JWT_SECRET（>=32B，与 APISIX 插件同源；禁止硬编码/入日志）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Any

_ISSUER = "agent-platform"
_PBKDF2_ITERATIONS = 200_000
_ACCESS_TTL_SECONDS = 30 * 60          # access token 30 分钟
_MIN_SECRET_LEN = 32


class LocalJwtError(ValueError):
    """签发配置或令牌校验失败（调用方映射为 401/500）。"""


def _secret() -> str:
    s = os.getenv("JWT_SECRET", "")
    if len(s) < _MIN_SECRET_LEN:
        raise LocalJwtError("JWT_SECRET 未配置或长度不足 32 字节")
    return s


def _b64url(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return urlsafe_b64decode(data + pad)


# ── JWT（HS512）──────────────────────────────────────────────

def issue_access_token(*, user_id: int, username: str, dept: str = "",
                       device_id: str = "", roles: list[str] | None = None,
                       ttl_seconds: int = _ACCESS_TTL_SECONDS) -> dict:
    """签发 access token。返回 {token, expiresIn(ms), exp}。

    roles：角色数组（viewer/editor/admin，来源 auth.users.role，对齐
    prompts.py::_check_permission 权限矩阵）。写入 payload["roles"] 供
    前端 resolve_operator_role() 单点消费（2026-09-15 跨会话协同 §5.1）。
    """
    now = int(time.time())
    exp = now + ttl_seconds
    payload = {"userId": user_id, "username": username, "dept": dept,
               "roles": roles or ["viewer"],
               "type": "access", "deviceId": device_id,
               "iss": _ISSUER, "iat": now, "exp": exp}
    header = {"alg": "HS512", "typ": "JWT"}
    signing_input = (_b64url(json.dumps(header).encode()) + "." +
                     _b64url(json.dumps(payload).encode()))
    sig = _b64url(hmac.new(_secret().encode(), signing_input.encode(), hashlib.sha512).digest())
    return {"token": f"{signing_input}.{sig}", "expiresIn": ttl_seconds * 1000, "exp": exp}


def verify_access_token(token: str) -> dict[str, Any] | None:
    """验签 + exp 校验（自签自验，宽差 30s）。失败返回 None，不抛异常。

    用途：logout 解析 Bearer（决定是否写黑名单）。业务鉴权仍由 APISIX 插件执行，
    本函数不承担网关职责。
    """
    try:
        secret = _secret()
        h, p, s = token.split(".")
        signing_input = f"{h}.{p}"
        expect = _b64url(hmac.new(secret.encode(), signing_input.encode(), hashlib.sha512).digest())
        if not hmac.compare_digest(expect, s):
            return None
        payload = json.loads(_b64url_decode(p))
        if payload.get("iss") != _ISSUER:
            return None
        if int(payload.get("exp", 0)) + 30 < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def token_ttl_seconds(token: str) -> int:
    """黑名单 TTL 用：剩余有效期（失败返回 0）。"""
    payload = verify_access_token(token)
    if not payload:
        return 0
    return max(0, int(payload["exp"]) - int(time.time()))


# ── 口令哈希（pbkdf2_hmac-SHA256）────────────────────────────

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt),
                                 _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """常量时间比较；格式不符返回 False（不抛）。"""
    try:
        algo, iter_s, salt, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        expect = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt),
                                     int(iter_s))
        return hmac.compare_digest(expect.hex(), digest_hex)
    except Exception:
        return False


def new_refresh_token() -> tuple[str, str]:
    """生成刷新令牌。返回 (明文, sha256_hex)——库中只存哈希。"""
    raw = secrets.token_urlsafe(48)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()

