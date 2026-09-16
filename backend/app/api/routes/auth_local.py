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

import os
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import bindparam, text

from backend.app.api.deps import (
    OperatorIdentity,
    require_admin_user,
    resolve_operator_role,
)

from contextlib import asynccontextmanager

from backend.memory.database import get_session
from backend.security import local_jwt
from backend.security.local_jwt import (
    hash_password,
    hash_refresh_token,
    issue_access_token,
    new_refresh_token,
    token_ttl_seconds,
    verify_access_token,
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


def _write_session(issued: dict) -> bool:
    """登录/刷新后写会话键 auth:session:{userId}:{jti}（TTL=token 剩余有效期）。

    方案 A 会话闸的写侧：键存在 = token 处于"已签发且未吊销"状态。
    尽力而为：Redis 不可用时记 warning（audit 灰度期无影响；enforce 前必须
    确认 Redis 稳定，否则该 token 会被网关会话闸拒绝——黑名单通道本就
    fail-closed，Redis 稳定性是同一前提）。
    """
    jti = issued.get("jti")
    if not jti:
        return False
    client = get_redis()
    if client is None:
        logger.warning("[local-auth] Redis 不可用，会话键未写入（jti=%s…，enforce 下该 token 将被拒）",
                       jti[:8])
        return False
    try:
        ttl = max(1, int(issued["exp"]) - int(time.time()))
        client.set(f"auth:session:{issued.get('userId')}:{jti}", "1", ex=ttl)
        return True
    except Exception:
        logger.warning("[local-auth] 会话键写入异常", exc_info=True)
        return False


def _revoke_session(token: str) -> None:
    """logout 时删除会话键（尽力而为；键不存在/Redis 不可用均静默——
    黑名单已兜底，本函数只是让会话闸立即生效，不等黑名单 TTL）。"""
    payload = verify_access_token(token)
    if not payload:
        return
    from backend.security.local_jwt import session_key
    key = session_key(payload)
    if not key:
        return
    client = get_redis()
    if client is None:
        return
    try:
        client.delete(key)
    except Exception:
        logger.warning("[local-auth] 会话键删除异常", exc_info=True)


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
    _write_session(issued)
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
    _write_session(issued)
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
        _revoke_session(token)   # 会话闸：立即删键（方案 A）
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


# ── 安全运营（2026-09-16 方案 A 配套：管理员只读 + 会话强制下线）──────────
#
# JWT 单通道的运营面：在线会话、灰度开关状态、敏感端点清单。语义边界：
# - 会话 = Redis `auth:session:{userId}:{jti}`（方案 A 会话闸键空间）。
#   "强制下线" = 删键：enforce 下下一次请求即被网关/后端会话闸拒绝（401）；
#   audit 灰度期删键不产生实际拦截（闸只记日志），页面已提示。
# - 网关侧 GATEWAY_SESSION_CHECK 是 APISIX 容器的部署层 env，app 进程读不到，
#   返回 mode=None 由前端展示部署说明——不猜测运行值。
# - 三个端点统一挂 require_admin_user（kind==user 且 role==admin）：安全运营
#   接口不开放给服务凭据通道（service 不该管理用户会话）。

_SECURITY_ENDPOINT_GUARDS = (
    "require_admin_user",
    "require_user_actor",
    "require_admin_operator",
)

# 内联守卫（handler 体内 await require_admin_operator(request)）运行时扫描
# 不到，此清单人工维护；与动态扫描结果按 (path, methods) 合并，runtime 优先。
# observability 三项见 2026-09-16 审计页测试报告 §敏感端点清单。
_SECURITY_ENDPOINTS_CURATED = [
    {"path": "/api/observability/gateway-auth", "methods": ["GET"],
     "guard": "require_admin_operator", "source": "curated"},
    {"path": "/api/observability/gateway-access-logs", "methods": ["GET"],
     "guard": "require_admin_operator", "source": "curated"},
    {"path": "/api/observability/system-health", "methods": ["GET"],
     "guard": "require_admin_operator", "source": "curated"},
    # admin_tasks.py 为并发会话开发中模块（未提交），是否生效以其合并为准
    {"path": "/api/admin/tasks", "methods": ["GET"],
     "guard": "require_admin_operator", "source": "curated"},
]


def _scan_guarded_endpoints(app) -> list[dict]:
    """扫描 FastAPI 路由表，找出以统一守卫作为 Depends 的端点（运行时口径）。"""
    out: list[dict] = []
    for route in getattr(app, "routes", []):
        dep = getattr(route, "dependant", None)
        if dep is None:
            continue
        guard = None
        for d in getattr(dep, "dependencies", []) or []:
            name = getattr(getattr(d, "call", None), "__name__", "")
            if name in _SECURITY_ENDPOINT_GUARDS:
                guard = name
                break
        if guard is None:
            continue
        methods_raw = getattr(route, "methods", None) or set()
        methods = sorted(methods_raw - {"HEAD", "OPTIONS"})
        out.append({"path": route.path, "methods": methods,
                    "guard": guard, "source": "runtime"})
    return out


async def _attach_usernames(parsed: list[dict]) -> None:
    """按 userId 批量补 username/realName/role（expanding IN，单次查询）。"""
    uids = sorted({p["userId"] for p in parsed})
    if not uids:
        return
    async with _db() as session:
        rows = (await session.execute(
            text("SELECT id, username, real_name, role FROM auth.users "
                 "WHERE id IN :ids").bindparams(bindparam("ids", expanding=True)),
            {"ids": uids})).mappings().all()
    names = {r["id"]: r for r in rows}
    for p in parsed:
        u = names.get(p["userId"])
        p["username"] = u["username"] if u else None
        p["realName"] = ((u["real_name"] if u else None) or p["username"]) if u else None
        p["role"] = u["role"] if u else None


@sys_router.get("/security/overview")
async def security_overview(request: Request,
                            operator: OperatorIdentity = Depends(require_admin_user)):
    """灰度开关状态 + 敏感端点清单（只读）。"""
    runtime = _scan_guarded_endpoints(request.app)
    seen = {(e["path"], tuple(e["methods"])) for e in runtime}
    endpoints = runtime + [e for e in _SECURITY_ENDPOINTS_CURATED
                           if (e["path"], tuple(e["methods"])) not in seen]
    return _result({
        "modes": {
            "jwtSessionGuard": {
                "mode": os.getenv("JWT_SESSION_GUARD_MODE", "audit").strip().lower(),
                "scope": "backend-middleware",
                "note": "off/audit/enforce（默认 audit）；改 .env 后需重启 app 容器",
            },
            "sensitiveApiGuard": {
                "mode": os.getenv("SENSITIVE_API_GUARD_MODE", "enforce").strip().lower(),
                "scope": "backend-deps",
                "note": "audit/enforce；改 .env 后需重启 app 容器",
            },
            "gatewaySessionCheck": {
                "mode": None,
                "scope": "apisix-container",
                "note": "部署层 env（GATEWAY_SESSION_CHECK，默认 audit），app 进程读不到；"
                        "切换 runbook 见 docs/2026-09-16-方案A-JWT单通道实施报告.md",
            },
        },
        "endpoints": endpoints,
        "actor": operator.actor,
    })


@sys_router.get("/security/sessions")
async def list_sessions(operator: OperatorIdentity = Depends(require_admin_user)):
    """在线会话列表（扫 Redis auth:session:*，按 userId 联表补用户信息）。"""
    client = get_redis()
    if client is None:
        return _result({"sessions": [], "redisAvailable": False})
    try:
        keys = list(client.scan_iter(match="auth:session:*", count=200))
    except Exception:
        logger.warning("[security-ops] 会话扫描异常", exc_info=True)
        return _result({"sessions": [], "redisAvailable": False})

    parsed: list[dict] = []
    for k in keys:
        rest = k[len("auth:session:"):]
        uid_s, sep, jti = rest.partition(":")
        if not sep or not uid_s.isdigit() or not jti:
            continue
        parsed.append({"key": k, "userId": int(uid_s), "jti": jti,
                       "ttlSeconds": client.ttl(k)})
    await _attach_usernames(parsed)
    parsed.sort(key=lambda x: (x["userId"], x["jti"]))
    return _result({"sessions": parsed, "redisAvailable": True})


@sys_router.delete("/security/sessions/{user_id}/{jti}")
async def force_logout(user_id: int, jti: str,
                       operator: OperatorIdentity = Depends(require_admin_user)):
    """强制下线：删除该用户的会话键（方案 A 会话闸即刻生效）。"""
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", jti):
        return _fail("jti 格式非法", code=400)
    client = get_redis()
    if client is None:
        return _fail("Redis 不可用，无法强制下线", code=503)
    try:
        deleted = bool(client.delete(f"auth:session:{user_id}:{jti}"))
    except Exception:
        logger.warning("[security-ops] 强制下线删除键异常", exc_info=True)
        return _fail("Redis 操作失败，无法强制下线", code=503)
    logger.info(f"[security-ops] 强制下线 actor={operator.actor} "
                f"userId={user_id} jti={jti[:8]}… deleted={deleted}")
    return _result({"revoked": deleted, "userId": user_id, "jti": jti})
