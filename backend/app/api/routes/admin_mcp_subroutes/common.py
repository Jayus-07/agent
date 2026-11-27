"""admin_mcp_subroutes/common.py — MCP 控制台数据库操作、安全校验与业务辅助。"""
from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlparse

from fastapi import Request
from fastapi.responses import JSONResponse
from psycopg2.extras import RealDictCursor

from backend.app.api.deps import OperatorIdentity
from backend.app.api.identity import resolve_identity
from backend.app.api.routes.admin_mcp_subroutes.models import (
    SEMANTIC_HINT_MAX,
    SEMANTIC_ITEM_MAX,
    SEMANTIC_LIST_MAX,
    SEMANTIC_SUMMARY_MAX,
    _SENSITIVE_QUERY_KEYS,
)
from backend.shared.admin_llm import redact_urls as _redact_urls
from backend.shared.crypto import encrypt_secret, fingerprint, last4
from backend.shared.logger import logger


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


# ═══════════════════════════════════════════════════
# HTTP 响应辅助
# ═══════════════════════════════════════════════════

def _bad_request(message: str, code: str = "INVALID_PARAM"):
    return JSONResponse(status_code=400, content={"code": code, "message": message})


def _not_found(message: str = "资源不存在"):
    return JSONResponse(status_code=404, content={"code": "NOT_FOUND", "message": message})


def _conflict(message: str):
    return JSONResponse(status_code=409, content={"code": "CONFLICT", "message": message})


# ═══════════════════════════════════════════════════
# URL 与安全校验
# ═══════════════════════════════════════════════════

def _validate_connection_url(base_url: str, network_scope: str) -> str:
    """URL 安全校验（SSRF）：scheme 白名单 + DNS 解析逐 IP 拦截。"""
    from backend.tools.url_guard import UrlBlockedError, assert_url_allowed

    try:
        return assert_url_allowed(
            base_url, allow_private=(network_scope == "private"))
    except UrlBlockedError as exc:
        raise ValueError(f"MCP 地址被 SSRF 防护拦截: {exc}") from exc


def _validate_egress_allowlist(base_url: str) -> None:
    """G3 出口白名单：配置了 MCP_EGRESS_ALLOWLIST 时 host 必须命中。

    防「影子服务器」双保险的第二层：SSRF 只拦私网/非 http（公网任意
    host 放行），白名单把声明面收窄到部署方批准的出口。未配置不启用
    （历史行为零变化）；模式支持精确 host 与 *.suffix 后缀通配。只在
    连接声明面（create/update）前置，存量连接运行时出网不受影响。
    """
    from backend.config.mcp import egress_allowlist

    patterns = egress_allowlist()
    if not patterns:
        return
    host = (urlparse(base_url).hostname or "").strip().lower()
    for pat in patterns:
        if pat.startswith("*."):
            suffix = pat[1:]  # ".example.com"
            if host.endswith(suffix):
                return
        elif host == pat:
            return
    raise ValueError(
        f"MCP 出口白名单未放行该 host: {host or '(无法解析)'}"
        f"（已配置 {len(patterns)} 条模式，见 MCP_EGRESS_ALLOWLIST）"
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_semantic_draft(content: str) -> dict[str, Any] | None:
    """解析 LLM 输出为语义契约草稿；形状不合法返回 None（调用方重试）。"""
    from backend.shared.json_extractor import extract_json

    if not content or "{" not in content:
        return None
    parsed = extract_json(content, source="tool.semantic_suggest")
    if not isinstance(parsed, dict):
        return None
    summary = str(parsed.get("summary") or "").strip()
    if not summary:
        return None

    def _items(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        items = []
        for entry in value:
            text = str(entry or "").strip()
            if text:
                items.append(text[:SEMANTIC_ITEM_MAX])
        return items[:SEMANTIC_LIST_MAX]

    hint = parsed.get("capability_hint")
    hint_text = str(hint).strip()[:SEMANTIC_HINT_MAX] \
        if isinstance(hint, str) and hint.strip() else None
    return {
        "summary": summary[:SEMANTIC_SUMMARY_MAX],
        "scenarios": _items(parsed.get("scenarios")),
        "exclusions": _items(parsed.get("exclusions")),
        "capability_hint": hint_text,
    }


def _validate_semantic_contract(summary: str, scenarios: list,
                                exclusions: list,
                                capability_hint: str | None) -> str | None:
    """草稿保存的长度校验；命中返回可读原因（端点转 400）。"""
    if not summary.strip():
        return "summary 不能为空"
    if len(summary.strip()) > SEMANTIC_SUMMARY_MAX:
        return f"summary 超过 {SEMANTIC_SUMMARY_MAX} 字"
    for name, items in (("scenarios", scenarios), ("exclusions", exclusions)):
        if len(items) > SEMANTIC_LIST_MAX:
            return f"{name} 条数超过 {SEMANTIC_LIST_MAX}"
        for item in items:
            if len(str(item).strip()) > SEMANTIC_ITEM_MAX:
                return f"{name} 单条超过 {SEMANTIC_ITEM_MAX} 字"
    if capability_hint is not None and \
            len(str(capability_hint).strip()) > SEMANTIC_HINT_MAX:
        return f"capability_hint 超过 {SEMANTIC_HINT_MAX} 字"
    return None


def _render_semantic_prompt(row: dict, input_schema: dict) -> str:
    """组装语义建议 prompt：仅工具元数据，全部经 URL 清洗。"""
    from backend.prompts.service import prompt_service

    return prompt_service.render_sync(
        "tool.semantic_suggest",
        tool_name=_redact_urls(str(row["tool_name"] or "")),
        description=_redact_urls(str(row["description"] or "")),
        input_schema=_redact_urls(json.dumps(input_schema or {},
                                             ensure_ascii=False)),
        examples=_redact_urls("\n".join(str(e) for e in (row.get("examples")
                                                         or []))),
        connection_name=_redact_urls(str(row.get("connection_name") or "")),
        governance_operation=str(
            (row.get("governance") or {}).get("operation") or "read"),
    ).text


def _embedded_credential_reason(base_url: str) -> str | None:
    """检查 URL 内嵌凭据：命中返回原因（创建/更新入口据此拒绝）。"""
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


# ═══════════════════════════════════════════════════
# 连接查询与凭据辅助
# ═══════════════════════════════════════════════════

_CONNECTION_SELECT = """
    SELECT c.id, c.name, c.base_url, c.transport, c.auth_type,
           c.network_scope, c.enabled, c.status, c.last_error,
           c.last_synced_at, c.discovery_version, c.tenant_id,
           c.created_by, c.updated_by, c.updated_at,
           c.last_checked_at, c.probe_fail_count,
           k.secret_fingerprint, k.secret_last4, k.secret_version
    FROM ai.mcp_connections c
    LEFT JOIN ai.mcp_connection_credentials k ON k.connection_id = c.id
"""


def _load_connection(conn_id: str, tenant_id: str) -> dict | None:
    """按 ID + 租户取连接行；跨租户/不存在统一 None（不泄露存在性）。"""
    from backend.app.api.routes.admin_mcp_subroutes.dynamic import current_query_one
    query_one = current_query_one(_query_one)
    return query_one(
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
        "last_checked_at": row["last_checked_at"].isoformat()
        if row.get("last_checked_at") else None,
        "probe_fail_count": int(row.get("probe_fail_count") or 0),
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
    from backend.app.api.routes.admin_mcp_subroutes.dynamic import current_query_one
    query_one = current_query_one(_query_one)
    row = query_one(
        "SELECT secret_cipher FROM ai.mcp_connection_credentials "
        "WHERE connection_id = %(cid)s", {"cid": conn_id})
    if not row or not row["secret_cipher"]:
        return ""
    from backend.shared.crypto import decrypt_secret

    return decrypt_secret(row["secret_cipher"])


def _conn_headers(auth_type: str, secret: str) -> dict[str, str] | None:
    from backend.infra.mcp_registry.probe import conn_headers

    return conn_headers(auth_type, secret)


async def _refresh_runtime() -> None:
    from backend.infra.mcp_registry import store as mcp_store

    try:
        await mcp_store.refresh_now()
    except Exception:  # noqa: BLE001 — 刷新失败由轮询兜底
        logger.warning("[AdminMCP] 写后刷新运行时快照失败（轮询会重试）",
                       exc_info=True)


def _mark_connection_status(conn_id: str, status: str, error: str = "") -> None:
    from backend.app.api.routes.admin_mcp_subroutes.dynamic import current_execute
    execute_fn = current_execute(_execute)
    execute_fn(
        "UPDATE ai.mcp_connections SET status = %(status)s, "
        "last_error = %(err)s, updated_at = now() WHERE id = %(cid)s",
        {"status": status, "err": error[:1000], "cid": conn_id},
    )


def _record_probe_result(conn_id: str, tenant_id: str, ok: bool, error: str,
                         elapsed_ms: int, source: str) -> None:
    """探活结果统一落库（/test 手工探活与后台巡检同语义，决策 #16）：
    状态 + last_checked_at + 失败计数（退避依据）+ 健康事件。"""
    from backend.app.api.routes.admin_mcp_subroutes.dynamic import current_execute, current_query_one
    query_one = current_query_one(_query_one)
    execute_fn = current_execute(_execute)

    fail_count = 0
    if not ok:
        row = query_one(
            "SELECT probe_fail_count FROM ai.mcp_connections WHERE id = %(cid)s",
            {"cid": conn_id})
        fail_count = int((row or {}).get("probe_fail_count") or 0) + 1
    execute_fn(
        "UPDATE ai.mcp_connections SET status = %(status)s, "
        "last_error = %(err)s, last_checked_at = now(), "
        "probe_fail_count = %(fails)s, updated_at = now() WHERE id = %(cid)s",
        {"status": "connected" if ok else "error", "err": error[:1000],
         "fails": 0 if ok else fail_count, "cid": conn_id})
    execute_fn(
        "INSERT INTO ai.mcp_health_events "
        "(connection_id, tenant_id, ok, error, elapsed_ms, source) "
        "VALUES (%(cid)s, %(tenant)s, %(ok)s, %(err)s, %(ms)s, %(src)s)",
        {"cid": conn_id, "tenant": tenant_id, "ok": ok,
         "err": error[:1000], "ms": int(elapsed_ms), "src": source})


def _probe_http_api(
    base_url: str, headers: dict[str, str] | None,
) -> tuple[int, bool, str]:
    """HTTP API 连接探活（委托 infra.probe 规范实现，巡检器同源）。"""
    from backend.infra.mcp_registry.probe import probe_http_api

    return probe_http_api(base_url, headers)


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
    from backend.app.api.routes.admin_mcp_subroutes.dynamic import current_execute, current_query_one
    query_one = current_query_one(_query_one)
    execute_fn = current_execute(_execute)

    cipher = encrypt_secret(secret)
    existing = query_one(
        "SELECT secret_version FROM ai.mcp_connection_credentials "
        "WHERE connection_id = %(cid)s", {"cid": conn_id})
    if existing:
        execute_fn(
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
        execute_fn(
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
# 工具视图与查询辅助
# ═══════════════════════════════════════════════════

_TOOL_SELECT = """
    SELECT t.*, c.name AS connection_name, c.enabled AS conn_enabled,
           c.transport AS conn_transport
    FROM ai.mcp_tools t
    JOIN ai.mcp_connections c ON c.id = t.connection_id
"""


def _load_tool(tool_id: int, tenant_id: str) -> dict | None:
    from backend.app.api.routes.admin_mcp_subroutes.dynamic import current_query_one
    query_one = current_query_one(_query_one)
    return query_one(
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
        # AI 语义契约（决策 #19）：草稿候选 + 已并入留痕（独立列，不动 governance）
        "semantic_draft": row.get("semantic_draft"),
        "semantic_approved": row.get("semantic_approved"),
        "last_seen_version": row["last_seen_version"],
        "retired_reason": row["retired_reason"],
        "updated_at": row["updated_at"].isoformat()
        if row.get("updated_at") else None,
    }


def _tool_list_view(row: dict) -> dict:
    """目录列表行的精简视图（/admin/mcp/tools、/connections/{id}/tools）。

    只保留列表/抽屉摘要与编辑预填需要的字段；hash、审核留痕、语义契约
    （semantic_draft/semantic_approved）、examples 等大字段只随
    GET /admin/mcp/tools/{id} 详情下发，列表 N 行时不再整表搬 JSON。
    invocation 保留：/tools 来源徽标与 HTTP 调用定义编辑预填依赖它。
    """
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
        "schema_changed": bool(row["schema_changed"]),
        "governance": row.get("governance") or {},
        "invocation": row.get("invocation") or {},
    }


def _current_input_schema(tool_id: int) -> dict:
    from backend.app.api.routes.admin_mcp_subroutes.dynamic import current_query_one
    query_one = current_query_one(_query_one)
    row = query_one(
        "SELECT input_schema FROM ai.mcp_tool_snapshots "
        "WHERE tool_id = %(tid)s ORDER BY discovery_version DESC, "
        "id DESC LIMIT 1", {"tid": tool_id})
    return (row["input_schema"] or {}) if row else {}


def _export_payload(tenant: str) -> dict[str, Any]:
    """导出/发布快照共用的声明载荷（同构，D7 红线）。

    红线（ADR-0011 D7）：凭据密文、fingerprint、last4 绝不出现；URL 内嵌
    凭据（user:pass@host / ?api_key= / #fragment）统一净化，无法安全净化
    的连接进 rejected_connections——不能以「未读取 secret_cipher」推断
    载荷无密钥。
    """
    from backend.app.api.routes.admin_mcp_subroutes.dynamic import current_query_all
    query_all = current_query_all(_query_all)

    connections: list[dict[str, Any]] = []
    redacted_connections: list[dict[str, Any]] = []
    rejected_connections: list[dict[str, Any]] = []
    for r in query_all(
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
    bundles = query_all(
        "SELECT b.id, b.name, b.description, b.domain, b.enabled "
        "FROM ai.capability_bundles b ORDER BY b.name")
    items = query_all(
        "SELECT bundle_id, capability FROM ai.capability_bundle_items")
    grants = query_all(
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
