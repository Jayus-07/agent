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
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import text

from backend.app.api.deps import (
    OperatorIdentity,
    require_admin_user,
    resolve_operator_role,
)
from backend.services import sys_config

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
# 并发刷新宽限期（2026-09-19 会话实体改造）：多标签页共享同一 refresh cookie，
# 轮换后短时间内旧 hash 被再次使用属正常竞态而非泄露——宽限期内不轮换、
# 不动 cookie，按会话当前状态补发 access token；超期重放才撤销整个会话。
_REFRESH_GRACE_SECONDS = 60
_REVOKE_REASON_REPLACED = "replaced"            # 同设备重新登录替换
_REVOKE_REASON_LOGOUT = "logout"                # 用户主动登出
_REVOKE_REASON_ADMIN = "admin_force_logout"     # 管理员强制下线
_REVOKE_REASON_REPLAY = "replay_detected"       # refresh token 重放（疑似泄露）
_COOKIE_KWARGS = {"key": "refresh_token", "httponly": True, "samesite": "lax",
                  "path": "/api/auth", "max_age": _REFRESH_TTL_SECONDS}


def _client_ip(request: Request) -> str:
    """取客户端 IP：优先 BFF 代理注入的 X-Client-IP（frontend */api/[...path]
    按浏览器连接算出，最接近终端用户），其次网关链路的 X-Real-IP / XFF 首段，
    兜底直连地址。

    IPv6 回环归一化为 127.0.0.1（::1 / ::ffff:x.x.x.x → 展示友好）。
    截断 64 字符与 auth.sessions.ip VARCHAR(64) 对齐；仅作台账展示，
    不参与任何访问控制决策（外部伪造该头只会污染自己的台账记录）。
    """
    client_ip = request.headers.get("x-client-ip")
    if client_ip:
        ip = client_ip.strip()
    else:
        real = request.headers.get("x-real-ip")
        if real:
            ip = real.strip()
        else:
            xff = request.headers.get("x-forwarded-for")
            ip = xff.split(",")[0].strip() if xff else ""
    if not ip:
        ip = (request.client.host if request.client else "") or ""
    if ip == "::1" or ip.startswith("::ffff:127.0.0."):
        ip = "127.0.0.1"
    elif ip.startswith("::ffff:"):
        ip = ip[len("::ffff:"):]
    return ip[:64]


def _session_idx_key(user_id, sid: str) -> str:
    """按 session 聚合 jti 的 Redis 索引键（强制下线 O(1) 定位）。"""
    return f"auth:session_idx:{user_id}:{sid}"


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
    """登录/刷新后写会话闸键 auth:session:{userId}:{jti}（TTL=token 剩余有效期）。

    方案 A 会话闸的写侧：键存在 = token 处于"已签发且未吊销"状态。
    同时维护 auth:session_idx:{userId}:{sid} 集合（2026-09-19 会话实体改造），
    记录该会话（auth.sessions.id）签发过的 jti，供按 session 强制下线时
    O(1) 定位删键；集合 TTL = refresh 有效期 + access 有效期，随活跃续期。

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
        uid = issued.get("userId")
        client.set(f"auth:session:{uid}:{jti}", "1", ex=ttl)
        sid = issued.get("sid") or ""
        if sid:
            idx = _session_idx_key(uid, sid)
            client.sadd(idx, jti)
            client.expire(idx, ttl + _REFRESH_TTL_SECONDS)
        return True
    except Exception:
        logger.warning("[local-auth] 会话键写入异常", exc_info=True)
        return False


def _revoke_session(token: str) -> None:
    """logout 时删除会话键并移出 session_idx（尽力而为；键不存在/Redis 不可用
    均静默——黑名单已兜底，本函数只是让会话闸立即生效，不等黑名单 TTL）。"""
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
        sid = payload.get("sid") or ""
        jti = payload.get("jti") or ""
        if sid and jti:
            client.srem(_session_idx_key(payload.get("userId"), sid), jti)
    except Exception:
        logger.warning("[local-auth] 会话键删除异常", exc_info=True)


def _delete_session_redis_keys(user_id, sid: str) -> int:
    """按 session 清 Redis：删除索引集合内全部 jti 闸键 + 索引本身。

    用于管理员强制下线 / 重放撤销 / 登出撤会话。尽力而为，返回删除的
    闸键数量；Redis 不可用返回 0（DB 侧吊销是权威，闸键随 access TTL 自然过期）。
    """
    client = get_redis()
    if client is None:
        return 0
    deleted = 0
    try:
        members = client.smembers(_session_idx_key(user_id, sid)) or set()
        for jti in members:
            deleted += client.delete(f"auth:session:{user_id}:{jti}")
        client.delete(_session_idx_key(user_id, sid))
    except Exception:
        logger.warning("[local-auth] 会话 Redis 键清理异常（sid=%s…）", sid[:8], exc_info=True)
    return deleted


async def _create_session(db, *, user_id: int, device_id: str, user_agent: str,
                          ip: str, ttl_seconds: int) -> str:
    """创建会话实体（一次设备登录），返回 session id（uuid 字符串）。"""
    row = (await db.execute(text(
        "INSERT INTO auth.sessions (user_id, device_id, user_agent, ip, refresh_expires_at) "
        "VALUES (:uid, :dev, :ua, :ip, now() + make_interval(secs => :ttl)) "
        "RETURNING id"),
        {"uid": user_id, "dev": device_id, "ua": user_agent, "ip": ip,
         "ttl": ttl_seconds})).mappings().first()
    return str(row["id"])


async def _revoke_session_row(db, *, session_id: str, reason: str) -> None:
    """吊销会话实体：置 revoked_at + 吊销该会话全部 refresh token（轮换链整体失效）。

    幂等：已吊销的会话不再改写 revoke_reason（首次吊销原因优先）。
    """
    await db.execute(text(
        "UPDATE auth.sessions SET revoked_at = now(), revoke_reason = :r "
        "WHERE id = :sid AND revoked_at IS NULL"),
        {"r": reason, "sid": session_id})
    await db.execute(text(
        "UPDATE auth.refresh_tokens SET revoked = TRUE, revoked_at = now() "
        "WHERE session_id = :sid AND revoked = FALSE"),
        {"sid": session_id})


# ── /auth/login ──────────────────────────────────────────────

@router.post("/login")
async def login(request: Request, response: Response):
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    device_id = (body.get("deviceId") or "")[:64]
    user_agent = (request.headers.get("user-agent") or "")[:256]
    ip = _client_ip(request)
    if not username or not password:
        return _fail("用户名或密码不能为空", code=400)

    async with _db() as session:
        row = await _fetch_user(session, username)
    if row is None or row["status"] != 1 or not verify_password(password, row["password_hash"]):
        return _fail("用户名或密码错误", code=400)

    async with _db() as session:
        # 同设备替换（2026-09-19 会话实体改造）：同一 user + device_id 的活跃
        # 会话先撤销再建新会话，不留多条同设备活跃会话。旧标签页的 access
        # token 在剩余 TTL 内仍有效，refresh 即 401（业务已接受该语义）。
        old = (await session.execute(text(
            "SELECT id FROM auth.sessions "
            "WHERE user_id = :uid AND device_id = :dev AND revoked_at IS NULL"),
            {"uid": row["id"], "dev": device_id})).mappings().first()
        if old:
            await _revoke_session_row(session, session_id=str(old["id"]),
                                      reason=_REVOKE_REASON_REPLACED)
            _delete_session_redis_keys(row["id"], str(old["id"]))
        sid = await _create_session(session, user_id=row["id"], device_id=device_id,
                                    user_agent=user_agent, ip=ip,
                                    ttl_seconds=_REFRESH_TTL_SECONDS)
        raw_refresh, token_hash = new_refresh_token()
        await session.execute(text(
            "INSERT INTO auth.refresh_tokens (user_id, token_hash, device_id, expires_at, session_id) "
            "VALUES (:uid, :th, :dev, now() + make_interval(secs => :ttl), :sid)"),
            {"uid": row["id"], "th": token_hash, "dev": device_id,
             "ttl": _REFRESH_TTL_SECONDS, "sid": sid})
        await session.commit()

    issued = issue_access_token(user_id=row["id"], username=row["username"],
                                dept=row["dept"], device_id=device_id,
                                roles=[row["role"]], session_id=sid)
    _write_session(issued)
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
            "SELECT rt.id, rt.user_id, rt.expires_at, rt.revoked, rt.revoked_at, rt.session_id, "
            "s.revoked_at AS session_revoked_at, s.device_id AS s_device_id, "
            "u.username, u.dept, u.role, u.status "
            "FROM auth.refresh_tokens rt "
            "LEFT JOIN auth.sessions s ON s.id = rt.session_id "
            "JOIN auth.users u ON u.id = rt.user_id "
            "WHERE rt.token_hash = :th"), {"th": token_hash})).mappings().first()

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        if row is None or row["status"] != 1 or row["expires_at"] <= now:
            return _fail("刷新凭据无效或已过期", code=401)

        sid = str(row["session_id"]) if row["session_id"] else ""

        if row["revoked"]:
            # 已吊销 token 的两种去向（2026-09-19 会话实体改造）：
            # ① 宽限期内同 hash 重用 = 多标签并发竞态：不轮换、不 set_cookie
            #   （浏览器 cookie 已由先到的请求更新），按会话当前状态补发
            #   access token——两个标签都成功且同一 session，不误吊销。
            # ② 超出宽限期的重放 = 疑似泄露：撤销整个会话（family 全吊销）。
            if (sid and row["session_revoked_at"] is None
                    and row["revoked_at"] is not None
                    and (now - row["revoked_at"]).total_seconds() <= _REFRESH_GRACE_SECONDS):
                issued = issue_access_token(user_id=row["user_id"], username=row["username"],
                                            dept=row["dept"], roles=[row["role"]],
                                            session_id=sid)
                _write_session(issued)
                await session.execute(text(
                    "UPDATE auth.sessions SET last_active_at = now() WHERE id = :sid"),
                    {"sid": sid})
                await session.commit()
                return _result({"token": issued["token"], "refreshToken": None,
                                "tokenType": "Bearer", "expiresIn": issued["expiresIn"],
                                "userInfo": {"userId": row["user_id"],
                                             "username": row["username"]}})
            if sid and row["session_revoked_at"] is None:
                await _revoke_session_row(session, session_id=sid,
                                          reason=_REVOKE_REASON_REPLAY)
                await session.commit()
                _delete_session_redis_keys(row["user_id"], sid)
                logger.warning("[local-auth] 检测到 refresh token 重放，已撤销整个会话 "
                               "userId=%s sid=%s…", row["user_id"], sid[:8])
            return _fail("刷新凭据无效或已过期", code=401)

        if sid and row["session_revoked_at"] is not None:
            # 会话已被替换/下线：旧家族凭据一律失效（不复活）
            return _fail("刷新凭据无效或已过期", code=401)

        # 正常轮换：吊销旧 token，新 token 继承同一 session_id（会话实体不变）
        await session.execute(text(
            "UPDATE auth.refresh_tokens SET revoked = TRUE, revoked_at = now() "
            "WHERE id = :id"), {"id": row["id"]})
        raw_new, token_hash_new = new_refresh_token()
        if sid:
            await session.execute(text(
                "INSERT INTO auth.refresh_tokens (user_id, token_hash, device_id, expires_at, session_id) "
                "VALUES (:uid, :th, :dev, now() + make_interval(secs => :ttl), :sid)"),
                {"uid": row["user_id"], "th": token_hash_new,
                 "dev": row["s_device_id"] or "", "ttl": _REFRESH_TTL_SECONDS, "sid": sid})
            # 会话随家族当前 token 滑动续期（台账与最新 refresh 行的 expires_at 对齐）
            await session.execute(text(
                "UPDATE auth.sessions SET last_active_at = now(), "
                "refresh_expires_at = now() + make_interval(secs => :ttl) "
                "WHERE id = :sid"), {"sid": sid, "ttl": _REFRESH_TTL_SECONDS})
        else:
            # 存量无 session 的旧凭据（023 上线前签发）：按旧逻辑轮换，
            # session_id 保持 NULL，7 天内自然淘汰，不强行归组
            await session.execute(text(
                "INSERT INTO auth.refresh_tokens (user_id, token_hash, device_id, expires_at) "
                "VALUES (:uid, :th, '', now() + make_interval(secs => :ttl))"),
                {"uid": row["user_id"], "th": token_hash_new, "ttl": _REFRESH_TTL_SECONDS})
        await session.commit()

    issued = issue_access_token(user_id=row["user_id"], username=row["username"],
                                dept=row["dept"], roles=[row["role"]], session_id=sid)
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
        # 会话实体口径（2026-09-19）：refresh cookie 是浏览器级共享，
        # 任一标签登出即整个浏览器会话结束——撤销整个 session（含轮换链），
        # 避免 /security 出现"家族已死但台账仍活跃"的僵尸会话。
        payload = verify_access_token(token)
        sid = (payload or {}).get("sid") or ""
        if sid:
            async with _db() as session:
                await _revoke_session_row(session, session_id=sid,
                                          reason=_REVOKE_REASON_LOGOUT)
                await session.commit()
            _delete_session_redis_keys((payload or {}).get("userId"), sid)
    raw = request.cookies.get("refresh_token")
    if raw:
        async with _db() as session:
            await session.execute(text(
                "UPDATE auth.refresh_tokens SET revoked = TRUE, revoked_at = now() "
                "WHERE token_hash = :th AND revoked = FALSE"),
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
                # 2026-09-16 动态化：DB 覆盖层（PUT /sys/config/{key} 免重启切换）
                **sys_config.get_info("JWT_SESSION_GUARD_MODE"),
                "configKey": "JWT_SESSION_GUARD_MODE",
                "scope": "backend-middleware",
                "note": "off/audit/enforce；DB 覆盖值 15s 内生效，回滚=写回旧值",
            },
            "sensitiveApiGuard": {
                **sys_config.get_info("SENSITIVE_API_GUARD_MODE"),
                "configKey": "SENSITIVE_API_GUARD_MODE",
                "scope": "backend-deps",
                "note": "audit/enforce；DB 覆盖值 15s 内生效，回滚=写回旧值",
            },
            "gatewaySessionCheck": {
                "mode": None,
                "source": "deployment",
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
    """在线会话列表（2026-09-19 会话实体改造：DB 口径）。

    一行 = 一次设备登录（auth.sessions），refresh 轮换/多标签/页面刷新
    均不产生新行。活跃口径：revoked_at IS NULL 且 refresh_expires_at 未到。
    不再扫 Redis——Redis 只是在线闸门，台账以数据库为准（Redis 故障不影响列表）。
    """
    async with _db() as session:
        rows = (await session.execute(text(
            "SELECT s.id, s.user_id, s.device_id, s.user_agent, s.ip, "
            "s.created_at, s.last_active_at, s.refresh_expires_at, "
            "u.username, u.real_name, u.role "
            "FROM auth.sessions s JOIN auth.users u ON u.id = s.user_id "
            "WHERE s.revoked_at IS NULL AND s.refresh_expires_at > now() "
            "ORDER BY s.user_id, s.created_at"))).mappings().all()
    sessions = [{
        "sessionId": str(r["id"]),
        "userId": r["user_id"],
        "username": r["username"],
        "realName": r["real_name"] or r["username"],
        "role": r["role"],
        "device": r["device_id"],
        "userAgent": r["user_agent"],
        "ip": r["ip"],
        "createdAt": r["created_at"].isoformat(),
        "lastActiveAt": r["last_active_at"].isoformat(),
        "expiresAt": r["refresh_expires_at"].isoformat(),
    } for r in rows]
    return _result({"sessions": sessions})


@sys_router.delete("/security/sessions/{session_id}")
async def force_logout(session_id: str,
                       operator: OperatorIdentity = Depends(require_admin_user)):
    """强制下线（按 session）：撤销会话实体 + 家族全部 refresh token +
    删除 Redis 内该会话全部 jti 闸键（enforce 下下一次请求即 401）。"""
    try:
        sid = str(uuid.UUID(session_id))
    except ValueError:
        return _fail("sessionId 格式非法", code=400)
    async with _db() as session:
        row = (await session.execute(text(
            "SELECT user_id, revoked_at FROM auth.sessions WHERE id = :sid"),
            {"sid": sid})).mappings().first()
        if row is None:
            return _fail("会话不存在", code=404)
        if row["revoked_at"] is None:
            await _revoke_session_row(session, session_id=sid,
                                      reason=_REVOKE_REASON_ADMIN)
            await session.commit()
    deleted = _delete_session_redis_keys(row["user_id"], sid)
    logger.info(f"[security-ops] 强制下线 actor={operator.actor} "
                f"userId={row['user_id']} sid={sid[:8]}… redisKeysDeleted={deleted}")
    return _result({"revoked": True, "userId": row["user_id"], "sessionId": sid})
