"""app/api/identity.py — 请求身份解析的单一入口（P3，docs/auth/03 五之二第 1 条）

此前身份解析散落各路由且口径不一：chat 是「请求体 user_id 优先、
TRUST_USER_HEADER 时头次之」（请求体可伪造），sql 是「开关+头」，
approvals 的 reviewer 干脆默认 "admin"。本模块把解析收敛为一个
helper，chat / sql / rag / approvals 统一调用；模式机见
backend/config/auth.py（legacy/header/strict）。

用法（路由内）::

    from backend.app.api.identity import resolve_identity, require_identity

    ident = resolve_identity(request, body_user_id=req.user_id)   # 三模式通用
    ident = require_identity(request)                             # strict 语义：未认证 401
"""
from dataclasses import dataclass

from fastapi import Request

from backend.config.auth import (
    AUTH_TYPE_HEADER,
    USER_DEPT_HEADER,
    USER_ID_HEADER,
    USER_NAME_HEADER,
    identity_source,
)


@dataclass(frozen=True)
class Identity:
    """一次请求解析出的身份。header/strict 下 body 身份字段永不落地。"""

    user_id: str = ""          # "" = 未认证（guest）
    user_name: str = ""
    department: str = ""
    auth_type: str = ""        # jwt | guest | ""（legacy 且无头时）
    source: str = ""           # 调试可读：header|body|default|guest

    @property
    def authenticated(self) -> bool:
        return bool(self.user_id)


# 网关 guest 模式（GATEWAY_AUTH_MODE=guest）对未认证请求注入的占位身份：
# X-Auth-Type: anonymous + X-User-Id: anonymous。不能当作真实用户，
# 否则记忆库/配额会按 "anonymous" 这个共享账号落库。
_ANONYMOUS = "anonymous"


def _from_headers(request: Request) -> Identity:
    uid = (request.headers.get(USER_ID_HEADER) or "").strip()
    auth_type = (request.headers.get(AUTH_TYPE_HEADER) or "").strip()
    if not uid or uid == _ANONYMOUS or auth_type == _ANONYMOUS:
        return Identity(user_id="", auth_type="guest", source="guest")
    return Identity(
        user_id=uid,
        user_name=(request.headers.get(USER_NAME_HEADER) or "").strip(),
        department=(request.headers.get(USER_DEPT_HEADER) or "").strip(),
        auth_type=auth_type or "jwt",
        source="header",
    )


def resolve_identity(request: Request, body_user_id: str | int | None = None) -> Identity:
    """按 IDENTITY_SOURCE 解析请求身份——全部路由的唯一身份入口。

    body_user_id 传请求体里的身份字段（legacy 用，header/strict 直接无视）。
    """
    mode = identity_source()

    if mode in ("header", "strict"):
        # 网关权威：请求体身份字段一律忽略（可伪造），头缺席即 guest。
        return _from_headers(request)

    # legacy：保持现网行为——请求体优先，TRUST_USER_HEADER=true 时头兜底。
    from backend.config import TRUST_USER_HEADER  # 旧开关，收敛期兼容

    uid = str(body_user_id or "").strip()
    if uid:
        return Identity(user_id=uid, auth_type="body", source="body")
    if TRUST_USER_HEADER:
        ident = _from_headers(request)
        if ident.authenticated:
            return ident
    return Identity(user_id="", auth_type="guest", source="guest")


def require_identity(request: Request) -> Identity:
    """strict 语义入口：解析后未认证直接 401（统一 JSON 由全局处理器接）。"""
    from fastapi import HTTPException

    ident = _from_headers(request)
    if not ident.authenticated:
        raise HTTPException(status_code=401, detail="未认证：缺少网关注入的身份头")
    return ident
