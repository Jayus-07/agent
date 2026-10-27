"""admin_mcp.py — MCP 能力中心管理端 API（B1/B2/B3/B4，ADR-0011）

| 端点 | 用途 |
|---|---|
| `GET  /admin/mcp/overview` | 运行时快照状态（生效工具/授权/刷新时间/派生错误） |
| `GET/POST /admin/mcp/connections` | 连接列表 / 新增（幂等键 + SSRF 校验 + 凭据加密） |
| `GET/PUT/DELETE /admin/mcp/connections/{id}` | 详情 / 编辑 / 删除（引用检查） |
| `POST /admin/mcp/connections/{id}/test` | 连接测试（initialize 握手，真实耗时与错误分类） |
| `POST /admin/mcp/connections/{id}/discover` | 工具发现（tools/list → 快照 → 漂移检测） |
| `POST /admin/mcp/connections/{id}/enable|disable` | 启停（运行时快照即时生效） |
| `GET  /admin/mcp/connections/{id}/tools` | 该连接的工具清单（含审核状态/漂移标记） |
| `GET  /admin/mcp/tools` | 全量 MCP 工具目录（统一目录数据源） |
| `GET/POST /admin/mcp/tools/{id}/...` | 工具详情 / 审核 / 停用 / 授权 / 测试调用 |

安全基线：
- 全部端点 admin 门禁（`require_admin_user`）；写操作带 Idempotency-Key
  走 `run_governance_mutation`（回调同步 + psycopg2，经 to_thread 执行）；
- 连接 URL 过 `url_guard`（SSRF；私网仅 network_scope='private' 放行）；
- 凭据 Fernet 密文分表存储，响应只回 fingerprint/last4，绝不回明文；
- 管理动作全部写 `ai.security_events`（ADMIN_ACTION + category=mcp_*）；
- 多租户：连接/工具/授权按管理员租户过滤，跨租户访问按不存在处理；
- 测试调用（test-invoke）走与生产完全相同的治理链，且仅限只读操作。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from psycopg2.extras import Json as PgJson, RealDictCursor
from pydantic import BaseModel, Field

from backend.app.api.deps import (
    OperatorIdentity,
    require_admin_user,
    run_governance_mutation,
)
from backend.app.api.identity import resolve_identity
from backend.infra.mcp_client import McpClientError, list_tools as mcp_list_tools
from backend.shared.crypto import encrypt_secret, fingerprint, last4
from backend.shared.logger import logger

router = APIRouter(prefix="/admin/mcp", tags=["Admin-MCP"])


# ═══════════════════════════════════════════════════
# DB 访问（同步 psycopg2；与 security_events 等旁路写同模式）
# ═══════════════════════════════════════════════════

@contextmanager
def _db() -> Iterator[Any]:
    from backend.infra.db import get_memory_engine

    raw = get_memory_engine().raw_connection()
    try:
        yield raw
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def _query_all(sql: str, params: dict | None = None) -> list[dict]:
    with _db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or {})
            return [dict(r) for r in cur.fetchall()]


def _query_one(sql: str, params: dict | None = None) -> dict | None:
    rows = _query_all(sql, params)
    return rows[0] if rows else None


def _execute(sql: str, params: dict | None = None) -> int:
    """执行写语句并返回受影响行数（CAS 更新按 0 判定并发冲突）。"""
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            return cur.rowcount


def _audit(event: str, detail: dict[str, Any], *, user_id: str = "",
           tenant_id: str = "") -> None:
    from backend.security.events import record_security_event

    try:
        record_security_event(
            "ADMIN_ACTION", category=event, detail=detail,
            user_id=user_id or None, tenant_id=tenant_id or None,
        )
    except Exception:  # noqa: BLE001 — 审计旁路软失败
        logger.warning("[AdminMCP] 审计写入失败: %s", event, exc_info=True)


def _tenant_of(operator: OperatorIdentity, request: Request) -> str:
    """操作者租户：OperatorIdentity 不携带租户，从网关注入头重新解析。

    同一请求内 resolve_identity 结果一致；无租户头（服务凭据通道）落
    default 租户——与其他治理写入口径一致。
    """
    ident = resolve_identity(request)
    return str(ident.tenant_id or "default")


def _actor_of(operator: OperatorIdentity) -> str:
    return str(operator.actor or "")


class _MutationConflict(RuntimeError):
    """幂等回调内的业务冲突（携带可读原因，转 409）。"""


# ═══════════════════════════════════════════════════
# 请求模型
# ═══════════════════════════════════════════════════

class ConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=512)
    transport: Literal["streamable_http", "http_api"] = "streamable_http"
    auth_type: Literal["none", "static_bearer"] = "none"
    secret: str = Field(default="", max_length=4096)  # 空串=不配置凭据
    network_scope: Literal["public", "private"] = "public"
    enabled: bool = True


class ConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    auth_type: Literal["none", "static_bearer"] | None = None
    # None=保持现有凭据；显式空串=清除
    secret: str | None = Field(default=None, max_length=4096)
    network_scope: Literal["public", "private"] | None = None
    enabled: bool | None = None


class ReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    # 平台可信描述（默认沿用上游描述，审核者可改写；进入 Planner/FC prompt）
    description: str = Field(default="", max_length=500)
    governance: dict[str, Any] = Field(default_factory=dict)
    examples: list[str] = Field(default_factory=list, max_length=10)
    notes: str = Field(default="", max_length=2000)


class GrantRequest(BaseModel):
    product_key: Literal["agent", "customer_service"]
    revoke: bool = False


class TestInvokeRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    product_key: Literal["agent", "customer_service", "internal"] = "internal"


class HttpToolRegisterRequest(BaseModel):
    """HTTP API 工具手工登记（http_api 连接专用；MCP 连接走发现）。

    tool_name/description/input_schema/invocation 全部是不可信输入：
    content_hash 绑定后由审核放行，capability_slug 平台归一。
    """
    tool_name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=500)
    invocation: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] = Field(default_factory=dict)


class HttpToolInvocationUpdateRequest(BaseModel):
    """HTTP API 工具调用定义编辑：产生新快照版本；approved 工具即漂移。"""
    description: str = Field(default="", max_length=500)
    invocation: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════
# 内部辅助
# ═══════════════════════════════════════════════════

def _bad_request(message: str, code: str = "INVALID_PARAM"):
    return JSONResponse(status_code=400, content={"code": code, "message": message})


def _not_found(message: str = "资源不存在"):
    return JSONResponse(status_code=404, content={"code": "NOT_FOUND", "message": message})


def _conflict(message: str):
    return JSONResponse(status_code=409, content={"code": "CONFLICT", "message": message})


def _validate_connection_url(base_url: str, network_scope: str) -> str:
    """URL 安全校验（SSRF）：scheme 白名单 + DNS 解析逐 IP 拦截。"""
    from backend.tools.url_guard import UrlBlockedError, assert_url_allowed

    try:
        return assert_url_allowed(
            base_url, allow_private=(network_scope == "private"))
    except UrlBlockedError as exc:
        raise ValueError(f"MCP 地址被 SSRF 防护拦截: {exc}") from exc


# URL 内嵌凭据红线（Batch 4 收口，PR #115 Codex P1）：连接地址不得携带
# userinfo（user:pass@host）或凭据型 query 参数（?api_key=...）——这类信息
# 会随 base_url 落库，并可能进入导出物、日志与 Trace；LLM/导出红线
# （ADR-0011 D7）要求任何出口都不含密钥，包括 URL 内嵌形态。
_SENSITIVE_QUERY_KEYS = frozenset({
    "api_key", "api-key", "apikey", "access_key", "access_token",
    "auth_key", "auth_token", "authorization", "credential", "credentials",
    "id_token", "key", "passwd", "password", "pwd", "refresh_token",
    "secret", "secret_key", "session_token", "sig", "signature", "token",
})


def _embedded_credential_reason(base_url: str) -> str | None:
    """检查 URL 内嵌凭据：命中返回原因（创建/更新入口据此拒绝）。"""
    from urllib.parse import parse_qsl, urlparse

    try:
        parsed = urlparse(str(base_url or ""))
    except ValueError:
        return "URL 无法解析"
    if parsed.username or parsed.password or "@" in (parsed.netloc or ""):
        return ("连接地址不得内嵌 userinfo（user:password@host），"
                "请在凭据中配置认证信息")
    for q_key, _ in parse_qsl(parsed.query, keep_blank_values=True):
        if q_key.strip().lower() in _SENSITIVE_QUERY_KEYS:
            return (f"连接地址 query 不得携带凭据型参数（{q_key}=...），"
                    "请在凭据中配置认证信息")
    if parsed.fragment:
        return "连接地址不得携带 fragment（#...）"
    return None


def _sanitize_export_url(base_url: str) -> tuple[str, list[str]] | None:
    """导出清洗（历史数据守门）：剔除 userinfo / 凭据型 query / fragment。

    返回 (净化后 URL, 剔除项列表)；无法安全净化（解析失败或净化后仍命中
    凭据样式）返回 None——调用方应将该连接拒绝导出并要求重新配置。
    """
    from urllib.parse import parse_qsl, urlencode, urlparse

    try:
        parsed = urlparse(str(base_url or ""))
    except ValueError:
        return None
    removed: list[str] = []
    netloc = parsed.netloc or ""
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
        removed.append("userinfo")
    kept_pairs = []
    for q_key, q_value in parse_qsl(parsed.query, keep_blank_values=True):
        if q_key.strip().lower() in _SENSITIVE_QUERY_KEYS:
            removed.append(f"query:{q_key}")
            continue
        kept_pairs.append((q_key, q_value))
    if not (parsed.hostname or "") or not netloc:
        return None
    cleaned = parsed._replace(
        netloc=netloc, query=urlencode(kept_pairs), fragment="",
    ).geturl()
    # 防御兜底：净化后仍命中凭据样式（编码变体等）→ 拒绝而非带病导出
    if _embedded_credential_reason(cleaned):
        return None
    return cleaned, removed


_CONNECTION_SELECT = """
    SELECT c.id, c.name, c.base_url, c.transport, c.auth_type,
           c.network_scope, c.enabled, c.status, c.last_error,
           c.last_synced_at, c.discovery_version, c.tenant_id,
           c.created_by, c.updated_by, c.updated_at,
           k.secret_fingerprint, k.secret_last4, k.secret_version
    FROM ai.mcp_connections c
    LEFT JOIN ai.mcp_connection_credentials k ON k.connection_id = c.id
"""


def _load_connection(conn_id: str, tenant_id: str) -> dict | None:
    """按 ID + 租户取连接行；跨租户/不存在统一 None（不泄露存在性）。"""
    return _query_one(
        f"{_CONNECTION_SELECT} WHERE c.id = %(cid)s AND c.tenant_id = %(tenant)s",
        {"cid": conn_id, "tenant": tenant_id},
    )


def _connection_view(row: dict) -> dict:
    """连接行的对外视图（不含任何密钥材料，仅指纹元数据）。"""
    return {
        "id": row["id"],
        "name": row["name"],
        "base_url": row["base_url"],
        "transport": row["transport"],
        "auth_type": row["auth_type"],
        "network_scope": row["network_scope"],
        "enabled": bool(row["enabled"]),
        "status": row["status"],
        "last_error": row["last_error"],
        "last_synced_at": row["last_synced_at"].isoformat()
        if row.get("last_synced_at") else None,
        "discovery_version": row["discovery_version"],
        "credential": {
            "configured": bool(row.get("secret_fingerprint")),
            "fingerprint": row.get("secret_fingerprint") or "",
            "last4": row.get("secret_last4") or "",
            "version": row.get("secret_version") or 0,
        },
        "tenant_id": row["tenant_id"],
        "created_by": row["created_by"],
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


def _connection_secret(conn_id: str) -> str:
    """解密连接凭据（仅测试/发现路径使用，绝不落日志）。"""
    row = _query_one(
        "SELECT secret_cipher FROM ai.mcp_connection_credentials "
        "WHERE connection_id = %(cid)s", {"cid": conn_id})
    if not row or not row["secret_cipher"]:
        return ""
    from backend.shared.crypto import decrypt_secret

    return decrypt_secret(row["secret_cipher"])


def _conn_headers(auth_type: str, secret: str) -> dict[str, str] | None:
    if auth_type == "static_bearer" and secret:
        return {"Authorization": f"Bearer {secret}"}
    return None


async def _refresh_runtime() -> None:
    from backend.infra.mcp_registry import store as mcp_store

    try:
        await mcp_store.refresh_now()
    except Exception:  # noqa: BLE001 — 刷新失败由轮询兜底
        logger.warning("[AdminMCP] 写后刷新运行时快照失败（轮询会重试）",
                       exc_info=True)


def _mark_connection_status(conn_id: str, status: str, error: str = "") -> None:
    _execute(
        "UPDATE ai.mcp_connections SET status = %(status)s, "
        "last_error = %(err)s, updated_at = now() WHERE id = %(cid)s",
        {"status": status, "err": error[:1000], "cid": conn_id},
    )


def _probe_http_api(
    base_url: str, headers: dict[str, str] | None,
) -> tuple[int, bool, str]:
    """HTTP API 连接探活：HEAD base_url，验证网络/TLS 可达性。

    任何 HTTP 响应（含 404/405）都算可达——根路径不要求有路由；
    连接/超时/TLS 失败才算不可达。trust_env=False 直连，与调用链一致。
    """
    import httpx

    started = time.perf_counter()
    try:
        with httpx.Client(trust_env=False, timeout=10.0) as client:
            response = client.head(str(base_url or ""), headers=headers or None,
                                   follow_redirects=True)
        elapsed = int((time.perf_counter() - started) * 1000)
        return elapsed, True, ""
    except Exception as exc:  # noqa: BLE001 — 探活收口全部网络异常
        elapsed = int((time.perf_counter() - started) * 1000)
        return elapsed, False, f"{type(exc).__name__}: {exc}"


def _classify_mcp_error(exc: Exception) -> str:
    message = str(exc)
    if "超时" in message or "Timeout" in type(exc).__name__:
        return "timeout"
    if "SSRF" in message:
        return "ssrf_blocked"
    if "握手" in message or "initialize" in message.lower():
        return "handshake_failed"
    if "工具报错" in message:
        return "upstream_tool_error"
    return "connection_failed"


def _upsert_credential(conn_id: str, secret: str, actor: str) -> None:
    cipher = encrypt_secret(secret)
    existing = _query_one(
        "SELECT secret_version FROM ai.mcp_connection_credentials "
        "WHERE connection_id = %(cid)s", {"cid": conn_id})
    if existing:
        _execute(
            """
            UPDATE ai.mcp_connection_credentials
            SET secret_cipher = %(cipher)s, secret_fingerprint = %(fp)s,
                secret_last4 = %(l4)s, secret_version = secret_version + 1,
                updated_by = %(actor)s, updated_at = now()
            WHERE connection_id = %(cid)s
            """,
            {"cipher": cipher, "fp": fingerprint(secret), "l4": last4(secret),
             "actor": actor, "cid": conn_id},
        )
    else:
        _execute(
            """
            INSERT INTO ai.mcp_connection_credentials
                (connection_id, secret_cipher, secret_fingerprint,
                 secret_last4, secret_version, updated_by)
            VALUES (%(cid)s, %(cipher)s, %(fp)s, %(l4)s, 1, %(actor)s)
            """,
            {"cid": conn_id, "cipher": cipher, "fp": fingerprint(secret),
             "l4": last4(secret), "actor": actor},
        )


# ═══════════════════════════════════════════════════
# Overview
# ═══════════════════════════════════════════════════

@router.get("/overview")
async def mcp_overview(request: Request):
    await require_admin_user(request)
    from backend.infra.mcp_registry import store as mcp_store

    snap = mcp_store.snapshot()
    return {
        "loaded": snap.loaded,
        "active_tools": sorted(snap.tools.keys()),
        "connection_count": len(snap.connections),
        "grant_count": len(snap.grants),
        "derive_errors": [{"capability": cap, "reason": reason}
                          for cap, reason in snap.errors],
        "refreshed_at": snap.refreshed_at,
    }


# ═══════════════════════════════════════════════════
# 连接管理（B1）
# ═══════════════════════════════════════════════════

@router.get("/connections")
async def list_connections(request: Request):
    operator = await require_admin_user(request)
    rows = await asyncio.to_thread(
        _query_all,
        f"{_CONNECTION_SELECT} WHERE c.tenant_id = %(tenant)s "
        "ORDER BY c.created_at DESC",
        {"tenant": _tenant_of(operator, request)},
    )
    return {"connections": [_connection_view(r) for r in rows]}


@router.post("/connections")
async def create_connection(request: Request, body: ConnectionCreate):
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    try:
        _validate_connection_url(body.base_url, body.network_scope)
    except ValueError as exc:
        return _bad_request(str(exc))
    cred_reason = _embedded_credential_reason(body.base_url)
    if cred_reason:
        return _bad_request(cred_reason)

    def _write() -> dict[str, Any]:
        conn_id = f"mcp_conn_{uuid.uuid4().hex[:12]}"
        dup = _query_one(
            "SELECT 1 AS one FROM ai.mcp_connections "
            "WHERE name = %(name)s AND tenant_id = %(tenant)s",
            {"name": body.name, "tenant": tenant})
        if dup:
            raise _MutationConflict(f"连接名已存在: {body.name}")
        _execute(
            """
            INSERT INTO ai.mcp_connections
                (id, name, base_url, transport, auth_type, network_scope,
                 enabled, status, tenant_id, created_by, updated_by)
            VALUES (%(id)s, %(name)s, %(url)s, %(transport)s, %(auth)s,
                    %(scope)s, %(enabled)s, 'never_connected',
                    %(tenant)s, %(actor)s, %(actor)s)
            """,
            {"id": conn_id, "name": body.name, "url": body.base_url,
             "transport": body.transport, "auth": body.auth_type,
             "scope": body.network_scope,
             "enabled": body.enabled, "tenant": tenant, "actor": actor})
        if body.auth_type == "static_bearer" and body.secret:
            _upsert_credential(conn_id, body.secret, actor)
        _audit("mcp_connection_create", {
            "connection_id": conn_id, "name": body.name,
            "transport": body.transport,
            "network_scope": body.network_scope,
            "credential_configured": bool(body.secret),
        }, user_id=actor, tenant_id=tenant)
        return {"id": conn_id}

    try:
        result = await asyncio.to_thread(
            run_governance_mutation, request, operator,
            "mcp_connection_create",
            {"name": body.name, "base_url": body.base_url}, _write)
    except _MutationConflict as exc:
        return _conflict(str(exc))
    await _refresh_runtime()
    return result


@router.get("/connections/{conn_id}")
async def get_connection(request: Request, conn_id: str):
    operator = await require_admin_user(request)
    row = await asyncio.to_thread(
        _load_connection, conn_id, _tenant_of(operator, request))
    if row is None:
        return _not_found("连接不存在")
    return _connection_view(row)


@router.put("/connections/{conn_id}")
async def update_connection(request: Request, conn_id: str,
                            body: ConnectionUpdate):
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    row = await asyncio.to_thread(_load_connection, conn_id, tenant)
    if row is None:
        return _not_found("连接不存在")

    next_url = body.base_url or row["base_url"]
    next_scope = body.network_scope or row["network_scope"]
    if body.base_url is not None:
        cred_reason = _embedded_credential_reason(body.base_url)
        if cred_reason:
            return _bad_request(cred_reason)
    if body.base_url is not None or body.network_scope is not None:
        try:
            _validate_connection_url(next_url, next_scope)
        except ValueError as exc:
            return _bad_request(str(exc))

    def _write() -> dict[str, Any]:
        enabled = row["enabled"] if body.enabled is None else body.enabled
        status = row["status"]
        if body.enabled is not None and body.enabled != row["enabled"]:
            status = "connected" if body.enabled else "disabled"
        _execute(
            """
            UPDATE ai.mcp_connections
            SET name = %(name)s, base_url = %(url)s, auth_type = %(auth)s,
                network_scope = %(scope)s, enabled = %(enabled)s,
                status = %(status)s, updated_by = %(actor)s, updated_at = now()
            WHERE id = %(cid)s
            """,
            {"name": body.name or row["name"], "url": next_url,
             "auth": body.auth_type or row["auth_type"], "scope": next_scope,
             "enabled": enabled, "status": status,
             "actor": actor, "cid": conn_id})
        if body.secret is not None:
            if body.secret:
                _upsert_credential(conn_id, body.secret, actor)
            else:
                _execute(
                    "DELETE FROM ai.mcp_connection_credentials "
                    "WHERE connection_id = %(cid)s", {"cid": conn_id})
        _audit("mcp_connection_update", {
            "connection_id": conn_id,
            "fields": [k for k, v in body.model_dump(exclude={"secret"}).items()
                       if v is not None],
            "credential_changed": body.secret is not None,
        }, user_id=actor, tenant_id=tenant)
        return {"id": conn_id, "updated": True}

    result = await asyncio.to_thread(
        run_governance_mutation, request, operator, "mcp_connection_update",
        {"connection_id": conn_id}, _write)
    await _refresh_runtime()
    return result


@router.delete("/connections/{conn_id}")
async def delete_connection(request: Request, conn_id: str):
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    row = await asyncio.to_thread(_load_connection, conn_id, tenant)
    if row is None:
        return _not_found("连接不存在")

    def _write() -> dict[str, Any]:
        refs = _query_one(
            """
            SELECT count(*) AS tools,
                   count(*) FILTER (WHERE status = 'approved') AS approved
            FROM ai.mcp_tools WHERE connection_id = %(cid)s
            """, {"cid": conn_id})
        grants = _query_one(
            """
            SELECT count(*) AS n FROM ai.mcp_tool_grants g
            JOIN ai.mcp_tools t ON t.id = g.tool_id
            WHERE t.connection_id = %(cid)s AND g.enabled
            """, {"cid": conn_id})
        if (refs and refs["tools"]) or (grants and grants["n"]):
            raise _MutationConflict(
                f"连接仍有 {refs['tools']} 个工具记录"
                f"（{refs['approved']} 个已审核）与 {grants['n']} 条有效授权，"
                "请先停用并清理工具与授权")
        _execute("DELETE FROM ai.mcp_connections WHERE id = %(cid)s",
                 {"cid": conn_id})
        _audit("mcp_connection_delete", {"connection_id": conn_id},
               user_id=actor, tenant_id=tenant)
        return {"id": conn_id, "deleted": True}

    try:
        result = await asyncio.to_thread(
            run_governance_mutation, request, operator,
            "mcp_connection_delete", {"connection_id": conn_id}, _write)
    except _MutationConflict as exc:
        return _conflict(str(exc))
    await _refresh_runtime()
    return result


@router.post("/connections/{conn_id}/test")
async def test_connection(request: Request, conn_id: str):
    """连接测试：与发现/调用是不同操作；连接成功不代表任何工具获得执行权限。

    - streamable_http：MCP initialize 握手 + tools/list；
    - http_api：对 base_url 发起一次 HEAD 探活（404/405 也算可达——
      探活验证的是网络与 TLS 可达性，不要求根路径有路由）。
    """
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    row = await asyncio.to_thread(_load_connection, conn_id, tenant)
    if row is None:
        return _not_found("连接不存在")
    try:
        _validate_connection_url(row["base_url"], row["network_scope"])
    except ValueError as exc:
        return _bad_request(str(exc))

    secret = ""
    if row["auth_type"] == "static_bearer" and row.get("secret_fingerprint"):
        secret = await asyncio.to_thread(_connection_secret, conn_id)
        if not secret:
            return _bad_request("凭据解密失败，请重新配置", code="SECRET_ERROR")
    headers = _conn_headers(row["auth_type"], secret)
    started = time.perf_counter()

    if row.get("transport") == "http_api":
        elapsed_ms, ok, error = await asyncio.to_thread(
            _probe_http_api, row["base_url"], headers)
        if ok:
            await asyncio.to_thread(_mark_connection_status, conn_id, "connected")
            await _refresh_runtime()
            _audit("mcp_connection_test", {
                "connection_id": conn_id, "ok": True,
                "elapsed_ms": elapsed_ms, "transport": "http_api",
            }, user_id=actor, tenant_id=tenant)
            return {"ok": True, "elapsed_ms": elapsed_ms,
                    "transport": "http_api"}
        await asyncio.to_thread(
            _mark_connection_status, conn_id, "error", error)
        _audit("mcp_connection_test", {
            "connection_id": conn_id, "ok": False,
            "elapsed_ms": elapsed_ms, "transport": "http_api",
            "error_class": "connection_failed",
        }, user_id=actor, tenant_id=tenant)
        return {"ok": False, "elapsed_ms": elapsed_ms, "transport": "http_api",
                "error_class": "connection_failed", "error": error[:500]}

    try:
        tools = await asyncio.to_thread(
            mcp_list_tools, row["base_url"], timeout=15.0, headers=headers)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        await asyncio.to_thread(_mark_connection_status, conn_id, "connected")
        await _refresh_runtime()
        _audit("mcp_connection_test", {
            "connection_id": conn_id, "ok": True, "elapsed_ms": elapsed_ms,
            "tool_count": len(tools),
        }, user_id=actor, tenant_id=tenant)
        return {
            "ok": True, "elapsed_ms": elapsed_ms, "tool_count": len(tools),
            "tool_names": [t["name"] for t in tools][:50],
        }
    except Exception as exc:  # noqa: BLE001 — 测试端点收口全部异常
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        error_class = _classify_mcp_error(exc)
        await asyncio.to_thread(
            _mark_connection_status, conn_id, "error", str(exc))
        _audit("mcp_connection_test", {
            "connection_id": conn_id, "ok": False, "elapsed_ms": elapsed_ms,
            "error_class": error_class,
        }, user_id=actor, tenant_id=tenant)
        return {
            "ok": False, "elapsed_ms": elapsed_ms,
            "error_class": error_class, "error": str(exc)[:500],
        }


@router.post("/connections/{conn_id}/tools/manual")
async def register_http_tool(request: Request, conn_id: str,
                             body: HttpToolRegisterRequest):
    """手工登记 HTTP API 工具（仅 http_api 连接；MCP 连接走发现）。

    登记即产生 pending 工具（不可执行）；invocation 经受控校验并计入
    content_hash，审核通过后才进入运行时快照。
    """
    from backend.infra.mcp_registry import specs as spec_lib

    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    conn = await asyncio.to_thread(_load_connection, conn_id, tenant)
    if conn is None:
        return _not_found("连接不存在")
    if conn["transport"] != "http_api":
        return _bad_request(
            "手工登记仅支持 HTTP API 连接；MCP 连接请使用发现",
            code="TRANSPORT_MISMATCH")
    try:
        input_schema = spec_lib.normalize_input_schema(body.input_schema)
        invocation = spec_lib.validate_invocation(body.invocation, input_schema)
    except spec_lib.SpecDeriveError as exc:
        return _bad_request(f"调用定义非法: {exc}")

    slug = spec_lib.capability_slug(conn["name"], body.tool_name)

    def _write() -> dict[str, Any]:
        with _db() as db:
            with db.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM ai.mcp_tools WHERE capability_slug = %(slug)s",
                    {"slug": slug})
                if cur.fetchone():
                    raise _MutationConflict(f"capability 已存在: mcp.{slug}")
                cur.execute(
                    """
                    INSERT INTO ai.mcp_tools
                        (connection_id, tool_name, capability_slug,
                         description, status, content_hash, invocation,
                         last_seen_version, tenant_id)
                    VALUES (%(cid)s, %(tool)s, %(slug)s, %(desc)s,
                            'pending', %(digest)s, %(inv)s, 1, %(tenant)s)
                    RETURNING id
                    """,
                    {"cid": conn_id, "tool": body.tool_name, "slug": slug,
                     "desc": body.description[:spec_lib.MAX_DESCRIPTION_CHARS],
                     "digest": spec_lib.content_hash(
                         body.description, input_schema, invocation=invocation),
                     "inv": PgJson(invocation), "tenant": tenant})
                tool_row_id = cur.fetchone()[0]
                cur.execute(
                    """
                    INSERT INTO ai.mcp_tool_snapshots
                        (tool_id, discovery_version, content_hash,
                         input_schema, description, invocation)
                    VALUES (%(tid)s, 1, %(digest)s, %(schema)s, %(desc)s,
                            %(inv)s)
                    """,
                    {"tid": tool_row_id, "digest": spec_lib.content_hash(
                        body.description, input_schema, invocation=invocation),
                     "schema": PgJson(input_schema),
                     "desc": body.description,
                     "inv": PgJson(invocation)})
        _audit("mcp_http_tool_register", {
            "connection_id": conn_id, "tool_id": tool_row_id,
            "tool_name": body.tool_name, "capability": f"mcp.{slug}",
            "method": invocation.get("method"), "path": invocation.get("path"),
        }, user_id=actor, tenant_id=tenant)
        return {"tool_id": tool_row_id, "capability": f"mcp.{slug}",
                "status": "pending"}

    try:
        result = await asyncio.to_thread(
            run_governance_mutation, request, operator,
            "mcp_http_tool_register",
            {"connection_id": conn_id, "tool_name": body.tool_name}, _write)
    except _MutationConflict as exc:
        return _conflict(str(exc))
    await _refresh_runtime()
    return result


@router.put("/tools/{tool_id}/invocation")
async def update_http_tool_invocation(request: Request, tool_id: int,
                                      body: HttpToolInvocationUpdateRequest):
    """编辑 HTTP API 工具调用定义：产生新快照版本。

    pending 工具直接更新待审内容；approved 工具置 schema_changed=true
    （漂移，立即退出运行时，重审后恢复）——调用目标变化等同契约变化。
    """
    from backend.infra.mcp_registry import specs as spec_lib

    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    row = await asyncio.to_thread(_load_tool, tool_id, tenant)
    if row is None:
        return _not_found("工具不存在")
    if row.get("conn_transport") != "http_api":
        return _bad_request(
            "仅 HTTP API 工具支持编辑调用定义", code="TRANSPORT_MISMATCH")
    if row["status"] == "retired":
        return _conflict("上游已消失（retired）的工具不能编辑")
    description = body.description or row["description"]
    try:
        input_schema = spec_lib.normalize_input_schema(body.input_schema)
        invocation = spec_lib.validate_invocation(body.invocation, input_schema)
    except spec_lib.SpecDeriveError as exc:
        return _bad_request(f"调用定义非法: {exc}")
    digest = spec_lib.content_hash(description, input_schema, invocation=invocation)
    next_version = int(row["last_seen_version"] or 0) + 1

    def _write() -> dict[str, Any]:
        _execute(
            """
            UPDATE ai.mcp_tools
            SET description = %(desc)s, invocation = %(inv)s,
                content_hash = %(digest)s,
                schema_changed = (status = 'approved'),
                last_seen_version = %(version)s, updated_at = now()
            WHERE id = %(tid)s
            """,
            {"desc": description[:spec_lib.MAX_DESCRIPTION_CHARS],
             "inv": PgJson(invocation), "digest": digest,
             "version": next_version, "tid": tool_id})
        _execute(
            """
            INSERT INTO ai.mcp_tool_snapshots
                (tool_id, discovery_version, content_hash, input_schema,
                 description, invocation)
            VALUES (%(tid)s, %(version)s, %(digest)s, %(schema)s, %(desc)s,
                    %(inv)s)
            """,
            {"tid": tool_id, "version": next_version, "digest": digest,
             "schema": PgJson(input_schema),
             "desc": description[:spec_lib.MAX_DESCRIPTION_CHARS],
             "inv": PgJson(invocation)})
        _audit("mcp_http_tool_invocation_update", {
            "tool_id": tool_id, "capability": f"mcp.{row['capability_slug']}",
            "version": next_version,
            "drift": row["status"] == "approved",
        }, user_id=actor, tenant_id=tenant)
        return {"tool_id": tool_id, "content_hash": digest,
                "version": next_version,
                "schema_changed": row["status"] == "approved"}

    try:
        result = await asyncio.to_thread(
            run_governance_mutation, request, operator,
            "mcp_http_tool_invocation_update",
            {"tool_id": tool_id}, _write)
    except _MutationConflict as exc:
        return _conflict(str(exc))
    await _refresh_runtime()
    return result


@router.post("/connections/{conn_id}/discover")
async def discover_tools(request: Request, conn_id: str):
    """工具发现：tools/list → 快照入库 → 漂移检测。

    新工具默认 pending（不可执行）；上游内容变化使 approved 工具进入
    待重审；上游消失的工具转 retired。发现成功不代表可执行。
    """
    from backend.infra.mcp_registry import specs as spec_lib

    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    row = await asyncio.to_thread(_load_connection, conn_id, tenant)
    if row is None:
        return _not_found("连接不存在")
    try:
        _validate_connection_url(row["base_url"], row["network_scope"])
    except ValueError as exc:
        return _bad_request(str(exc))

    secret = ""
    if row["auth_type"] == "static_bearer" and row.get("secret_fingerprint"):
        secret = await asyncio.to_thread(_connection_secret, conn_id)
        if not secret:
            return _bad_request("凭据解密失败，请重新配置", code="SECRET_ERROR")
    headers = _conn_headers(row["auth_type"], secret)

    def _discover() -> dict[str, Any]:
        started = time.perf_counter()
        try:
            upstream = mcp_list_tools(row["base_url"], timeout=20.0,
                                      headers=headers)
        except Exception as exc:  # noqa: BLE001 — 上游失败必须显式返回
            _mark_connection_status(conn_id, "error", str(exc))
            _audit("mcp_connection_discover", {
                "connection_id": conn_id, "ok": False,
                "error_class": _classify_mcp_error(exc),
            }, user_id=actor, tenant_id=tenant)
            return {
                "ok": False, "error_class": _classify_mcp_error(exc),
                "error": str(exc)[:500],
            }

        with _db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                version = int(row["discovery_version"]) + 1
                cur.execute(
                    "SELECT id, tool_name, content_hash, status, "
                    "capability_slug FROM ai.mcp_tools "
                    "WHERE connection_id = %(cid)s", {"cid": conn_id})
                existing = {r["tool_name"]: dict(r) for r in cur.fetchall()}
                result: dict[str, list] = {
                    "new": [], "changed": [], "unchanged": [],
                    "skipped": [], "retired": [],
                }
                seen_names = set()
                for item in upstream:
                    name = str(item.get("name") or "").strip()
                    if not name:
                        result["skipped"].append(
                            {"tool": "", "reason": "上游工具名为空"})
                        continue
                    seen_names.add(name)
                    description = str(item.get("description") or "")
                    input_schema = item.get("input_schema") or {}
                    output_schema = item.get("output_schema")
                    digest = spec_lib.content_hash(
                        description, input_schema, output_schema)
                    prev = existing.get(name)
                    if prev is None:
                        slug = spec_lib.capability_slug(row["name"], name)
                        cur.execute(
                            "SELECT 1 FROM ai.mcp_tools "
                            "WHERE capability_slug = %(slug)s", {"slug": slug})
                        if cur.fetchone():
                            result["skipped"].append({
                                "tool": name,
                                "reason": f"capability slug 冲突: {slug}"})
                            continue
                        cur.execute(
                            """
                            INSERT INTO ai.mcp_tools
                                (connection_id, tool_name, capability_slug,
                                 description, status, content_hash,
                                 last_seen_version, tenant_id)
                            VALUES (%(cid)s, %(tool)s, %(slug)s, %(desc)s,
                                    'pending', %(digest)s, %(version)s,
                                    %(tenant)s)
                            RETURNING id
                            """,
                            {"cid": conn_id, "tool": name, "slug": slug,
                             "desc": description[:spec_lib.MAX_DESCRIPTION_CHARS],
                             "digest": digest, "version": version,
                             "tenant": tenant})
                        tool_row_id = cur.fetchone()["id"]
                        cur.execute(
                            """
                            INSERT INTO ai.mcp_tool_snapshots
                                (tool_id, discovery_version, content_hash,
                                 input_schema, output_schema, description)
                            VALUES (%(tid)s, %(version)s, %(digest)s,
                                    %(schema)s, %(oschema)s, %(desc)s)
                            """,
                            {"tid": tool_row_id, "version": version,
                             "digest": digest,
                             "schema": PgJson(input_schema),
                             "oschema": PgJson(output_schema)
                             if output_schema is not None else None,
                             "desc": description})
                        result["new"].append(
                            {"tool": name, "capability": f"mcp.{slug}"})
                        continue
                    # 已有工具：漂移检测
                    cur.execute(
                        "UPDATE ai.mcp_tools SET last_seen_version = %(v)s, "
                        "updated_at = now() WHERE id = %(tid)s",
                        {"v": version, "tid": prev["id"]})
                    if digest == prev["content_hash"]:
                        result["unchanged"].append({"tool": name})
                        continue
                    cur.execute(
                        """
                        INSERT INTO ai.mcp_tool_snapshots
                            (tool_id, discovery_version, content_hash,
                             input_schema, output_schema, description)
                        VALUES (%(tid)s, %(version)s, %(digest)s,
                                %(schema)s, %(oschema)s, %(desc)s)
                        """,
                        {"tid": prev["id"], "version": version,
                         "digest": digest, "schema": PgJson(input_schema),
                         "oschema": PgJson(output_schema)
                         if output_schema is not None else None,
                         "desc": description})
                    if prev["status"] == "approved":
                        # 上游内容变化：待重审（approved_hash 失配即运行时失效）
                        cur.execute(
                            "UPDATE ai.mcp_tools SET content_hash = %(d)s, "
                            "schema_changed = true, updated_at = now() "
                            "WHERE id = %(tid)s",
                            {"d": digest, "tid": prev["id"]})
                        result["changed"].append({
                            "tool": name,
                            "reason": "schema_changed_rereview_required"})
                    else:
                        cur.execute(
                            "UPDATE ai.mcp_tools SET content_hash = %(d)s, "
                            "updated_at = now() WHERE id = %(tid)s",
                            {"d": digest, "tid": prev["id"]})
                        result["changed"].append(
                            {"tool": name, "reason": "pending_updated"})
                # 上游消失的工具 → retired（approved 的立即退出运行时）
                for name, prev in existing.items():
                    if name in seen_names or prev["status"] == "retired":
                        continue
                    cur.execute(
                        "UPDATE ai.mcp_tools SET status = 'retired', "
                        "retired_reason = 'upstream_missing', "
                        "updated_at = now() WHERE id = %(tid)s",
                        {"tid": prev["id"]})
                    result["retired"].append({"tool": name})

                cur.execute(
                    "UPDATE ai.mcp_connections SET status = 'connected', "
                    "last_synced_at = now(), discovery_version = %(v)s, "
                    "last_error = '', updated_at = now() WHERE id = %(cid)s",
                    {"v": version, "cid": conn_id})
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        _audit("mcp_connection_discover", {
            "connection_id": conn_id, "ok": True, "elapsed_ms": elapsed_ms,
            "new": len(result["new"]), "changed": len(result["changed"]),
            "retired": len(result["retired"]), "version": version,
        }, user_id=actor, tenant_id=tenant)
        return {"ok": True, "elapsed_ms": elapsed_ms,
                "discovery_version": version, **result}

    result = await asyncio.to_thread(
        run_governance_mutation, request, operator,
        "mcp_connection_discover", {"connection_id": conn_id}, _discover)
    await _refresh_runtime()
    return result


@router.post("/connections/{conn_id}/enable")
async def enable_connection(request: Request, conn_id: str):
    return await _set_connection_enabled(request, conn_id, True)


@router.post("/connections/{conn_id}/disable")
async def disable_connection(request: Request, conn_id: str):
    return await _set_connection_enabled(request, conn_id, False)


async def _set_connection_enabled(request: Request, conn_id: str,
                                  enabled: bool):
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    row = await asyncio.to_thread(_load_connection, conn_id, tenant)
    if row is None:
        return _not_found("连接不存在")

    def _write() -> dict[str, Any]:
        _execute(
            "UPDATE ai.mcp_connections SET enabled = %(enabled)s, "
            "status = %(status)s, updated_by = %(actor)s, updated_at = now() "
            "WHERE id = %(cid)s",
            {"enabled": enabled,
             "status": "connected" if enabled else "disabled",
             "actor": actor, "cid": conn_id})
        _audit("mcp_connection_set_enabled", {
            "connection_id": conn_id, "enabled": enabled,
        }, user_id=actor, tenant_id=tenant)
        return {"id": conn_id, "enabled": enabled}

    result = await asyncio.to_thread(
        run_governance_mutation, request, operator,
        "mcp_connection_set_enabled",
        {"connection_id": conn_id, "enabled": enabled}, _write)
    await _refresh_runtime()
    return result


# ═══════════════════════════════════════════════════
# 工具清单与审核（B2）
# ═══════════════════════════════════════════════════

_TOOL_SELECT = """
    SELECT t.*, c.name AS connection_name, c.enabled AS conn_enabled,
           c.transport AS conn_transport
    FROM ai.mcp_tools t
    JOIN ai.mcp_connections c ON c.id = t.connection_id
"""


def _load_tool(tool_id: int, tenant_id: str) -> dict | None:
    return _query_one(
        f"{_TOOL_SELECT} WHERE t.id = %(tid)s AND t.tenant_id = %(tenant)s",
        {"tid": tool_id, "tenant": tenant_id},
    )


def _tool_view(row: dict) -> dict:
    return {
        "id": row["id"],
        "connection_id": row["connection_id"],
        "connection_name": row.get("connection_name"),
        "conn_transport": row.get("conn_transport") or "streamable_http",
        "tool_name": row["tool_name"],
        "capability": f"mcp.{row['capability_slug']}",
        "capability_slug": row["capability_slug"],
        "description": row["description"],
        "status": row["status"],
        "content_hash": row["content_hash"],
        "approved_hash": row["approved_hash"],
        "schema_changed": bool(row["schema_changed"]),
        "governance": row.get("governance") or {},
        "invocation": row.get("invocation") or {},
        "examples": row.get("examples") or [],
        "review_notes": row["review_notes"],
        "reviewed_by": row["reviewed_by"],
        "reviewed_at": row["reviewed_at"].isoformat()
        if row.get("reviewed_at") else None,
        "last_seen_version": row["last_seen_version"],
        "retired_reason": row["retired_reason"],
        "updated_at": row["updated_at"].isoformat()
        if row.get("updated_at") else None,
    }


@router.get("/connections/{conn_id}/tools")
async def list_connection_tools(request: Request, conn_id: str):
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)

    def _load() -> dict[str, Any]:
        conn = _load_connection(conn_id, tenant)
        if conn is None:
            return {}
        rows = _query_all(
            f"{_TOOL_SELECT} WHERE t.connection_id = %(cid)s "
            "AND t.tenant_id = %(tenant)s ORDER BY t.tool_name",
            {"cid": conn_id, "tenant": tenant})
        grants = _query_all(
            "SELECT tool_id, product_key, granted_by, granted_at "
            "FROM ai.mcp_tool_grants "
            "WHERE tenant_id = %(tenant)s AND enabled", {"tenant": tenant})
        grants_by_tool: dict[int, list[dict]] = {}
        for g in grants:
            grants_by_tool.setdefault(int(g["tool_id"]), []).append({
                "product_key": g["product_key"],
                "granted_by": g["granted_by"],
                "granted_at": g["granted_at"].isoformat(),
            })
        tools = []
        for r in rows:
            view = _tool_view(r)
            view["grants"] = grants_by_tool.get(int(r["id"]), [])
            tools.append(view)
        return {"tools": tools}

    data = await asyncio.to_thread(_load)
    if not data:
        return _not_found("连接不存在")
    return data


@router.get("/tools")
async def list_all_tools(request: Request, status_filter: str = ""):
    """统一工具目录的 MCP 段（/tools 页面合并展示数据源）。"""
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)

    def _load() -> list[dict]:
        where = "t.tenant_id = %(tenant)s"
        params: dict[str, Any] = {"tenant": tenant}
        if status_filter:
            where += " AND t.status = %(status)s"
            params["status"] = status_filter
        rows = _query_all(
            f"{_TOOL_SELECT} WHERE {where} ORDER BY t.updated_at DESC", params)
        # 授权产品随列表下发（/tools 统一目录的授权列；与连接内清单同口径）
        grants = _query_all(
            "SELECT tool_id, product_key FROM ai.mcp_tool_grants "
            "WHERE tenant_id = %(tenant)s AND enabled", {"tenant": tenant})
        grants_by_tool: dict[int, list[str]] = {}
        for g in grants:
            grants_by_tool.setdefault(int(g["tool_id"]), []).append(
                str(g["product_key"]))
        views = []
        for r in rows:
            view = _tool_view(r)
            view["grants"] = [
                {"product_key": p} for p in grants_by_tool.get(int(r["id"]), [])]
            views.append(view)
        return views

    return {"tools": await asyncio.to_thread(_load)}


@router.get("/tools/{tool_id}")
async def get_tool(request: Request, tool_id: int):
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)

    def _load() -> dict | None:
        row = _load_tool(tool_id, tenant)
        if row is None:
            return None
        snapshots = _query_all(
            """
            SELECT discovery_version, content_hash, input_schema,
                   output_schema, description, discovered_at
            FROM ai.mcp_tool_snapshots WHERE tool_id = %(tid)s
            ORDER BY discovery_version DESC, id DESC LIMIT 20
            """, {"tid": tool_id})
        grants = _query_all(
            "SELECT product_key, enabled, granted_by, granted_at, revoked_at "
            "FROM ai.mcp_tool_grants WHERE tool_id = %(tid)s "
            "ORDER BY granted_at DESC", {"tid": tool_id})
        view = _tool_view(row)
        view["snapshots"] = [{
            "discovery_version": s["discovery_version"],
            "content_hash": s["content_hash"],
            "input_schema": s["input_schema"],
            "output_schema": s["output_schema"],
            "description": s["description"],
            "discovered_at": s["discovered_at"].isoformat(),
        } for s in snapshots]
        view["grants"] = [{
            "product_key": g["product_key"], "enabled": bool(g["enabled"]),
            "granted_by": g["granted_by"],
            "granted_at": g["granted_at"].isoformat(),
            "revoked_at": g["revoked_at"].isoformat()
            if g["revoked_at"] else None,
        } for g in grants]
        return view

    view = await asyncio.to_thread(_load)
    if view is None:
        return _not_found("工具不存在")
    return view


def _current_input_schema(tool_id: int) -> dict:
    row = _query_one(
        "SELECT input_schema FROM ai.mcp_tool_snapshots "
        "WHERE tool_id = %(tid)s ORDER BY discovery_version DESC, "
        "id DESC LIMIT 1", {"tid": tool_id})
    return (row["input_schema"] or {}) if row else {}


@router.post("/tools/{tool_id}/review")
async def review_tool(request: Request, tool_id: int, body: ReviewRequest):
    """审核（B2）：approve 把治理配置与路由示例落库并绑定 approved_hash；
    reject 仅记录结论。未审核工具永不进入运行时快照。"""
    from backend.infra.mcp_registry import specs as spec_lib

    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    row = await asyncio.to_thread(_load_tool, tool_id, tenant)
    if row is None:
        return _not_found("工具不存在")
    if row["status"] == "retired":
        return _conflict("上游已消失（retired）的工具不能审核，请等待重新发现")

    # 审核前本地校验治理配置（fail-fast）
    capability = f"mcp.{row['capability_slug']}"
    if body.action == "approve":
        schema = await asyncio.to_thread(_current_input_schema, tool_id)
        try:
            spec_lib.derive_tool_spec(
                capability, body.description or row["description"],
                schema, body.governance)
            spec_lib.derive_capability_decl(
                capability, body.description or row["description"],
                schema, body.governance, body.examples)
            if row.get("conn_transport") == "http_api":
                # HTTP API 工具：审核绑定时刻再校验一次调用定义
                spec_lib.validate_invocation(
                    row.get("invocation") or {}, schema)
        except spec_lib.SpecDeriveError as exc:
            return _bad_request(f"治理配置非法: {exc}")

    def _write() -> dict[str, Any]:
        if body.action == "approve":
            description = body.description or row["description"]
            # 审核绑定所见版本（CAS）：读取后若发现流程改写 content_hash
            # （漂移）或工具转 retired，本次审核拒绝——绝不能用旧摘要
            # 覆盖新内容并清掉漂移标记，否则未审核的新 Schema 会进入运行时。
            updated = _execute(
                """
                UPDATE ai.mcp_tools
                SET status = 'approved', description = %(desc)s,
                    governance = %(gov)s, examples = %(examples)s,
                    review_notes = %(notes)s, reviewed_by = %(actor)s,
                    reviewed_at = now(),
                    approved_hash = %(digest)s, content_hash = %(digest)s,
                    schema_changed = false, updated_at = now()
                WHERE id = %(tid)s
                  AND content_hash = %(expected)s
                  AND status <> 'retired'
                """,
                {"desc": description[:spec_lib.MAX_DESCRIPTION_CHARS],
                 "gov": PgJson(body.governance),
                 "examples": PgJson(body.examples),
                 "notes": body.notes, "actor": actor,
                 "digest": row["content_hash"], "tid": tool_id,
                 "expected": row["content_hash"]})
            if updated == 0:
                raise _MutationConflict(
                    "审核期间发现内容已变化（上游 Schema 漂移或工具已退役），"
                    "请刷新后按最新版本重新审核")
        else:
            _execute(
                """
                UPDATE ai.mcp_tools
                SET status = 'rejected', review_notes = %(notes)s,
                    reviewed_by = %(actor)s, reviewed_at = now(),
                    updated_at = now()
                WHERE id = %(tid)s
                """,
                {"notes": body.notes, "actor": actor, "tid": tool_id})
        _audit("mcp_tool_review", {
            "tool_id": tool_id, "capability": capability,
            "action": body.action, "reviewed_by": actor,
            "governance": body.governance if body.action == "approve" else {},
            "examples_count": len(body.examples),
        }, user_id=actor, tenant_id=tenant)
        return {"tool_id": tool_id, "status": body.action + "d"}

    try:
        result = await asyncio.to_thread(
            run_governance_mutation, request, operator, "mcp_tool_review",
            {"tool_id": tool_id, "action": body.action}, _write)
    except _MutationConflict as exc:
        return _conflict(str(exc))
    await _refresh_runtime()
    return result


@router.post("/tools/{tool_id}/disable")
async def disable_tool(request: Request, tool_id: int):
    """停用工具：运行时快照立即移除（新调用被 tool_not_registered 拒绝），
    保留审核记录便于恢复。"""
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    row = await asyncio.to_thread(_load_tool, tool_id, tenant)
    if row is None:
        return _not_found("工具不存在")

    def _write() -> dict[str, Any]:
        _execute(
            "UPDATE ai.mcp_tools SET status = 'disabled', updated_at = now() "
            "WHERE id = %(tid)s", {"tid": tool_id})
        _audit("mcp_tool_disable", {"tool_id": tool_id}, user_id=actor,
               tenant_id=tenant)
        return {"tool_id": tool_id, "status": "disabled"}

    result = await asyncio.to_thread(
        run_governance_mutation, request, operator, "mcp_tool_disable",
        {"tool_id": tool_id}, _write)
    await _refresh_runtime()
    return result


# ═══════════════════════════════════════════════════
# Agent 授权（B3）
# ═══════════════════════════════════════════════════

@router.post("/tools/{tool_id}/grants")
async def grant_tool(request: Request, tool_id: int, body: GrantRequest):
    """授权 / 撤销：产品级授权（ADR-0002 服务端产品身份）。仅 approved
    工具可授权；撤销后该产品的新调用立即被拒绝。"""
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    row = await asyncio.to_thread(_load_tool, tool_id, tenant)
    if row is None:
        return _not_found("工具不存在")

    if not body.revoke and row["status"] != "approved":
        return _conflict(
            f"工具当前状态为 {row['status']}，只有 approved 工具可授权")

    if not body.revoke:
        # 决策 #15 业务域硬隔离：审核时登记的业务域（governance.domain）
        # 属目标产品禁行域 → 拒绝授权。未分类（domain 为空）不设限，
        # 兼容存量数据；bundle 路径已按 bundle.domain 校验。
        from backend.core.product_context import (
            _BUNDLE_DOMAIN_EXCLUSIVE_ROOTS,
            domain_exclusive_forbidden_roots,
        )

        tool_domain = str((row.get("governance") or {}).get("domain") or "")
        forbidden = domain_exclusive_forbidden_roots(body.product_key)
        if forbidden & _BUNDLE_DOMAIN_EXCLUSIVE_ROOTS.get(tool_domain, frozenset()):
            return _bad_request(
                f"工具业务域为 {tool_domain}，不可授权给 {body.product_key} "
                "产品入口（业务域硬隔离），请改审核业务域或授权对应产品")

    def _write() -> dict[str, Any]:
        if body.revoke:
            _execute(
                """
                UPDATE ai.mcp_tool_grants
                SET enabled = false, revoked_at = now()
                WHERE tool_id = %(tid)s AND product_key = %(product)s
                  AND tenant_id = %(tenant)s AND enabled
                """,
                {"tid": tool_id, "product": body.product_key,
                 "tenant": tenant})
        else:
            _execute(
                """
                INSERT INTO ai.mcp_tool_grants
                    (tool_id, product_key, enabled, granted_by, tenant_id)
                VALUES (%(tid)s, %(product)s, true, %(actor)s, %(tenant)s)
                ON CONFLICT (tool_id, product_key, tenant_id)
                DO UPDATE SET enabled = true, granted_by = %(actor)s,
                              granted_at = now(), revoked_at = NULL
                """,
                {"tid": tool_id, "product": body.product_key,
                 "actor": actor, "tenant": tenant})
        _audit("mcp_tool_grant", {
            "tool_id": tool_id, "product_key": body.product_key,
            "revoked": body.revoke,
            "capability": f"mcp.{row['capability_slug']}",
        }, user_id=actor, tenant_id=tenant)
        return {
            "tool_id": tool_id, "product_key": body.product_key,
            "granted": not body.revoke,
        }

    result = await asyncio.to_thread(
        run_governance_mutation, request, operator, "mcp_tool_grant",
        {"tool_id": tool_id, "product_key": body.product_key,
         "revoke": body.revoke}, _write)
    await _refresh_runtime()
    return result


# ═══════════════════════════════════════════════════
# 测试调用（B4）
# ═══════════════════════════════════════════════════

@router.post("/tools/{tool_id}/test-invoke")
async def test_invoke_tool(request: Request, tool_id: int,
                           body: TestInvokeRequest):
    """真实只读调用测试：走与生产完全相同的治理链（Schema/风险/限流/
    熔断/超时/审计），绝不允许写操作裸测。product_key 决定以哪个产品
    身份执行（internal=服务运维身份全放行；agent/customer_service 会
    执行真实授权检查，未授权即被拒——这正是要验证的行为）。"""
    from backend.core.request_context import (
        set_product_context,
        set_tool_tenant_id,
        set_tool_user_id,
    )
    from backend.core.tool_governance.guard import (
        ToolCallRequest,
        governance_runtime,
    )
    from backend.infra.mcp_registry import store as mcp_store
    from backend.skills.mcp_dynamic.skill import _McpDynamicToolAdapter

    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)
    row = await asyncio.to_thread(_load_tool, tool_id, tenant)
    if row is None:
        return _not_found("工具不存在")

    capability = f"mcp.{row['capability_slug']}"
    runtime = mcp_store.get_runtime(capability)
    if runtime is None:
        return _conflict(
            f"工具未在运行时生效（status={row['status']}, "
            f"schema_changed={row['schema_changed']}, "
            f"连接启用={row['conn_enabled']}），无法测试")
    operation = str((row.get("governance") or {}).get("operation") or "read")
    if operation != "read":
        return _conflict(
            f"测试调用仅限只读工具（当前 operation={operation}）；"
            "有副作用的操作必须走真实业务链路的确认/审批/幂等机制")

    adapter = _McpDynamicToolAdapter(capability)
    request_obj = ToolCallRequest(
        capability=capability,
        arguments=dict(body.arguments or {}),
        user_id=actor or "admin-test",
        tenant_id=tenant,
    )
    set_product_context(body.product_key, "admin_mcp_test")
    set_tool_tenant_id(tenant)
    set_tool_user_id(actor or "admin-test")
    started = time.perf_counter()
    try:
        result = await governance_runtime.execute(
            request_obj,
            lambda: asyncio.to_thread(adapter.invoke, dict(body.arguments or {})),
            tool_name=capability,
            trace_agent="admin-mcp-test",
            trace_capability=capability,
        )
    finally:
        set_product_context("internal", "internal_call")
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    _audit("mcp_tool_test_invoke", {
        "tool_id": tool_id, "capability": capability,
        "product_key": body.product_key,
        "status": result.status.value, "elapsed_ms": elapsed_ms,
        "error_code": result.error_code,
    }, user_id=actor, tenant_id=tenant)
    return {
        "tool_id": tool_id,
        "capability": capability,
        "product_key": body.product_key,
        "tool_status": result.status.value,
        "error_code": result.error_code or "",
        "error_message": result.error_message or "",
        "elapsed_ms": elapsed_ms,
        "result": result.data,
    }


def _export_payload(tenant: str) -> dict[str, Any]:
    """导出/发布快照共用的声明载荷（同构，D7 红线）。

    红线（ADR-0011 D7）：凭据密文、fingerprint、last4 绝不出现；URL 内嵌
    凭据（user:pass@host / ?api_key= / #fragment）统一净化，无法安全净化
    的连接进 rejected_connections——不能以「未读取 secret_cipher」推断
    载荷无密钥。
    """
    connections: list[dict[str, Any]] = []
    redacted_connections: list[dict[str, Any]] = []
    rejected_connections: list[dict[str, Any]] = []
    for r in _query_all(
            "SELECT name, base_url, transport, auth_type, network_scope "
            "FROM ai.mcp_connections WHERE tenant_id = %(t)s "
            "ORDER BY name", {"t": tenant}):
        view = {
            "name": r["name"],
            "base_url": r["base_url"],
            "transport": r["transport"],
            "auth_type": r["auth_type"],
            "network_scope": r["network_scope"],
        }
        cred_reason = _embedded_credential_reason(r["base_url"])
        sanitized = _sanitize_export_url(r["base_url"])
        if sanitized is None:
            # 解析失败/无 host/净化后仍命中凭据样式 → 拒绝导出
            rejected_connections.append({
                "name": r["name"],
                "reason": cred_reason or "URL 无法安全净化",
            })
            continue
        cleaned_url, removed = sanitized
        if removed:
            view["base_url"] = cleaned_url
            redacted_connections.append(
                {"name": r["name"], "removed": removed})
        connections.append(view)
    bundles = _query_all(
        "SELECT b.id, b.name, b.description, b.domain, b.enabled "
        "FROM ai.capability_bundles b ORDER BY b.name")
    items = _query_all(
        "SELECT bundle_id, capability FROM ai.capability_bundle_items")
    grants = _query_all(
        "SELECT bundle_id, product_key FROM ai.product_bundle_grants "
        "WHERE enabled AND revoked_at IS NULL AND tenant_id = %(t)s",
        {"t": tenant})
    items_by_bundle: dict[str, list[str]] = {}
    for i in items:
        items_by_bundle.setdefault(str(i["bundle_id"]), []).append(
            str(i["capability"]))
    grants_by_bundle: dict[str, list[str]] = {}
    for g in grants:
        grants_by_bundle.setdefault(str(g["bundle_id"]), []).append(
            str(g["product_key"]))
    bundle_views = [{
        "name": b["name"],
        "description": b["description"],
        "domain": b["domain"],
        "capabilities": items_by_bundle.get(str(b["id"]), []),
        "granted_products": grants_by_bundle.get(str(b["id"]), []),
    } for b in bundles]
    return {
        "version": 1,
        "tenant": tenant,
        "connections": connections,
        "capability_bundles": bundle_views,
        "redacted_connections": redacted_connections,
        "rejected_connections": rejected_connections,
    }


@router.get("/export")
async def export_configuration(request: Request):
    """配置导出（Batch 4，只读）：连接声明 + 能力包 + 授权关系，目标环境
    重新映射与审核的输入。

    红线（ADR-0011 D7）：
      - 凭据密文、fingerprint、last4 **绝不导出**——目标环境重新配置；
      - URL 内嵌凭据（user:pass@host / ?api_key= / #fragment）导出前净化，
        无法安全净化的连接拒绝导出（见 redacted/rejected_connections）；
      - 工具快照/审核状态/审批历史不导出——审核必须在新环境重新进行；
      - 导出内容含授权关系（bundle→product），由导入方管理员确认后重建。
    """
    operator = await require_admin_user(request)
    tenant = _tenant_of(operator, request)
    actor = _actor_of(operator)

    def _load() -> dict[str, Any]:
        payload = _export_payload(tenant)
        _audit("mcp_configuration_export", {
            "connections": len(payload["connections"]),
            "bundles": len(payload["capability_bundles"]),
            "redacted": len(payload["redacted_connections"]),
            "rejected": len(payload["rejected_connections"]),
        }, user_id=actor, tenant_id=tenant)
        return {
            "version": 1,
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "tenant": tenant,
            "connections": payload["connections"],
            "capability_bundles": payload["capability_bundles"],
            "redacted_connections": payload["redacted_connections"],
            "rejected_connections": payload["rejected_connections"],
        }

    return await asyncio.to_thread(_load)


__all__ = ["router"]
