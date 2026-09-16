"""routes/auth_local.py — py 自建用户体系（2026-09-15 项目拆分）。

替代退役的 Java auth-service/system-service，前端契约 1:1 对齐
（frontend/src/lib/auth.ts 的 Result 包裹 + HttpOnly Cookie 语义）：

- POST /auth/login    {username,password,deviceId?}
    → Result{data:{token, refreshToken:null, tokenType:"Bearer",
                   expiresIn(ms), userInfo:{userId,username,realName}}}
      成功时种 HttpOnly Cookie refresh_token（Path=/api/auth, 7d）
- POST /auth/refresh  凭 Cookie 轮换（旧 token 吊销 + 新 cookie）
- POST /auth/logout   Bearer(access) → 写 Redis 黑名单 + 吊销 refresh + 清 Cookie
- POST /sys/users/register {username,password,confirmPassword,realName?}
    → Result{data:{userId,username,realName,message}}

鉴权边界：
- 本路由组为公开端点（已加入 api_key_middleware 与 APISIX 白名单）；
  业务请求的 JWT 鉴权仍由 APISIX gateway-auth 执行（issuer=agent-platform）。
- access 黑名单写入 get_redis()（logout 时尽力而为，TTL=剩余有效期）；
  Redis 不可用时 logout 仍成功（access 30min 自然过期兜底），但不静默——记 warning。
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import text

from backend.app.api.deps import OperatorIdentity, resolve_operator_role

from contextlib import asynccontextmanager

from backend.memory.database import get_session
from backend.security import local_jwt
from backend.security.local_jwt import (
    hash_password,
    hash_refresh_token,
    issue_access_token,
    new_refresh_token,
    token_ttl_seconds,
    verify_password,
)
from backend.infra.redis.client import get_redis
from backend.shared.logger import logger

router = APIRouter(prefix="/auth", tags=["认证"])
sys_router = APIRouter(prefix="/sys", tags=["用户中心"])

_REFRESH_TTL_SECONDS = 7 * 24 * 3600
_COOKIE_KWARGS = {"key": "refresh_token", "httponly": True, "samesite": "lax",
                  "path": "/api/auth", "max_age": _REFRESH_TTL_SECONDS}


@asynccontextmanager
async def _db():
    """get_session 是 async 生成器（供 Depends 使用），这里包一层供 async with 使用。

    提交在本模块内显式执行（break 退出生成器会跳过其内置 commit）。
    """
    async for session in get_session():
        yield session
        break


def _result(data, code: int = 200, message: str = "success") -> dict:
    """对齐 Java Result 包裹（前端 unwrapResult 消费）。"""
    return {"code": code, "message": message, "data": data, "timestamp": int(time.time() * 1000)}


def _fail(message: str, code: int = 400):
    from fastapi.responses import JSONResponse
    return JSONResponse(_result(None, code=code, message=message), status_code=code)


async def _fetch_user(session, username: str):
    row = (await session.execute(text(
        "SELECT id, username, password_hash, real_name, dept, role, status "
        "FROM auth.users WHERE username = :u"), {"u": username})).mappings().first()
    return row


def _blacklist_access(token: str) -> bool:
    """logout 黑名单写入（尽力而为）。返回是否写入成功。"""
    ttl = token_ttl_seconds(token)
    if ttl <= 0:
        return False
    client = get_redis()
    if client is None:
        logger.warning("[local-auth] Redis 不可用，logout 未能写黑名单（token 将于剩余 TTL 后自然过期）")
        return False
    try:
        client.set(f"auth:blacklist:{token}", "1", ex=ttl)
        return True
    except Exception:
        logger.warning("[local-auth] 黑名单写入异常", exc_info=True)
        return False


# ── /auth/login ──────────────────────────────────────────────

@router.post("/login")
async def login(request: Request, response: Response):
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    device_id = (body.get("deviceId") or "")[:64]
    if not username or not password:
        return _fail("用户名或密码不能为空", code=400)

    async with _db() as session:
        row = await _fetch_user(session, username)
    if row is None or row["status"] != 1 or not verify_password(password, row["password_hash"]):
        return _fail("用户名或密码错误", code=400)

    issued = issue_access_token(user_id=row["id"], username=row["username"],
                                dept=row["dept"], device_id=device_id,
                                roles=[row["role"]])
    raw_refresh, token_hash = new_refresh_token()
    async with _db() as session:
        await session.execute(text(
            "INSERT INTO auth.refresh_tokens (user_id, token_hash, device_id, expires_at) "
            "VALUES (:uid, :th, :dev, now() + make_interval(secs => :ttl))"),
            {"uid": row["id"], "th": token_hash, "dev": device_id,
             "ttl": _REFRESH_TTL_SECONDS})
        await session.commit()

    response.set_cookie(value=raw_refresh, **_COOKIE_KWARGS)
    return _result({
        "token": issued["token"],
        "refreshToken": None,          # 契约：改走 HttpOnly Cookie
        "tokenType": "Bearer",
        "expiresIn": issued["expiresIn"],
        "userInfo": {"userId": row["id"], "username": row["username"],
                     "realName": row["real_name"] or row["username"],
                     "roles": [row["role"]]},
    })


# ── /auth/refresh（轮换）─────────────────────────────────────

@router.post("/refresh")
async def refresh(request: Request, response: Response):
    raw = request.cookies.get("refresh_token")
    if not raw:
        return _fail("缺少刷新凭据", code=401)
    token_hash = hash_refresh_token(raw)

    async with _db() as session:
        row = (await session.execute(text(
            "SELECT rt.id, rt.user_id, rt.expires_at, rt.revoked, u.username, u.dept, u.role, u.status "
            "FROM auth.refresh_tokens rt JOIN auth.users u ON u.id = rt.user_id "
            "WHERE rt.token_hash = :th"), {"th": token_hash})).mappings().first()
        from datetime import datetime, timezone
        if row is None or row["revoked"] or row["expires_at"] <= datetime.now(timezone.utc) or row["status"] != 1:
            return _fail("刷新凭据无效或已过期", code=401)
        # 轮换：吊销旧 token，签发新对
        await session.execute(text(
            "UPDATE auth.refresh_tokens SET revoked = TRUE WHERE id = :id"), {"id": row["id"]})
        raw_new, token_hash_new = new_refresh_token()
        await session.execute(text(
            "INSERT INTO auth.refresh_tokens (user_id, token_hash, device_id, expires_at) "
            "VALUES (:uid, :th, '', now() + make_interval(secs => :ttl))"),
            {"uid": row["user_id"], "th": token_hash_new, "ttl": _REFRESH_TTL_SECONDS})
        await session.commit()

    issued = issue_access_token(user_id=row["user_id"], username=row["username"],
                                dept=row["dept"], roles=[row["role"]])
    response.set_cookie(value=raw_new, **_COOKIE_KWARGS)
    return _result({"token": issued["token"], "refreshToken": None,
                    "tokenType": "Bearer", "expiresIn": issued["expiresIn"],
                    "userInfo": {"userId": row["user_id"], "username": row["username"]}})


# ── /auth/logout ─────────────────────────────────────────────

@router.post("/logout")
async def logout(request: Request, response: Response):
    authz = request.headers.get("authorization") or ""
    token = authz[7:].strip() if authz[:7].lower() == "bearer " else ""
    if token:
        _blacklist_access(token)
    raw = request.cookies.get("refresh_token")
    if raw:
        async with _db() as session:
            await session.execute(text(
                "UPDATE auth.refresh_tokens SET revoked = TRUE WHERE token_hash = :th"),
                {"th": hash_refresh_token(raw)})
            await session.commit()
    response.delete_cookie(**{k: v for k, v in _COOKIE_KWARGS.items() if k != "max_age"})
    return _result(True)


# ── /sys/users/register（对齐前端 register 契约）─────────────

@sys_router.post("/users/register")
async def register(request: Request):
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    confirm = body.get("confirmPassword") or ""
    real_name = (body.get("realName") or "").strip()[:50]

    if not (3 <= len(username) <= 20):
        return _fail("用户名长度需为 3-20 个字符", code=400)
    if not (6 <= len(password) <= 20):
        return _fail("密码长度需为 6-20 个字符", code=400)
    if password != confirm:
        return _fail("两次输入的密码不一致", code=400)

    async with _db() as session:
        exists = (await session.execute(text(
            "SELECT 1 FROM auth.users WHERE username = :u"), {"u": username})).first()
        if exists:
            return _fail("用户名已存在", code=400)
        row = (await session.execute(text(
            "INSERT INTO auth.users (username, password_hash, real_name, role) "
            "VALUES (:u, :p, :r, 'viewer') RETURNING id, username, real_name, role"),
            {"u": username, "p": hash_password(password), "r": real_name})).mappings().first()
        await session.commit()

    logger.info(f"[local-auth] 注册用户 id={row['id']} username={row['username']}")
    return _result({"userId": row["id"], "username": row["username"],
                    "realName": row["real_name"], "message": "注册成功"})


_ALLOWED_ROLES = ("viewer", "editor", "admin")


@sys_router.patch("/users/{user_id}/role")
async def change_role(user_id: int, request: Request,
                      operator: "OperatorIdentity" = Depends(resolve_operator_role)):
    """变更用户角色（提权/降权）。仅 admin 可操作（resolve_operator_role 双通道）。

    - 变更即时落库；目标用户已签发的 access token（30min TTL）与 refresh
      不回收，新角色在下次登录/刷新时进入 JWT roles claim 生效。
    - 首个 admin 无法由本接口产生（鸡生蛋）：用 SQL 一次性提权
      `UPDATE auth.users SET role='admin' WHERE username='...'`，之后即可界面化管理。
    """
    if operator.role != "admin":
        raise HTTPException(status_code=403, detail="仅 admin 可变更用户角色")

    body = await request.json()
    role = (body.get("role") or "").strip()
    if role not in _ALLOWED_ROLES:
        return _fail(f"角色必须是 {'/'.join(_ALLOWED_ROLES)}", code=400)

    async with _db() as session:
        row = (await session.execute(text(
            "UPDATE auth.users SET role = :role WHERE id = :uid "
            "RETURNING id, username, role"),
            {"role": role, "uid": user_id})).mappings().first()
        await session.commit()
    if row is None:
        return _fail("用户不存在", code=404)

    logger.info(f"[local-auth] 角色变更 actor={operator.actor} "
                f"user={row['username']}({row['id']}) → {row['role']}")
    return _result({"userId": row["id"], "username": row["username"],
                    "role": row["role"], "changedBy": operator.actor})
