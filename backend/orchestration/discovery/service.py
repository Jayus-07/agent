"""service.py — 管理端 Tool DiscoveryService（决策 #18，B1）

只读候选解析 + 策略存取。设计边界（诚实声明）：
  - 复用既有原语：域候选 = hierarchical.resolve_domain_tools（域未知时
    回退 effective_manifest 的 planner 可见全集），权限收窄 =
    CapabilityRouter._authorized_candidates（角色/scope 空集 + 产品域
    禁行根语义，与执行期门同谓词）；本模块不新增路由实现；
  - 零运行时改动：B1 不接入 /agent 路由链路，仅供管理端策略 API 与
    模拟器使用——「模拟结果」描述的是当前策略 + 权限下的候选解析视图，
    不承诺线上行为；
  - 身份注入：product_allows_capability 内部（动态 MCP / bundle 授权
    快照）经 request_context ContextVar 取租户，调用点以 set/reset
    包裹注入（测试可脱离 DB 走默认值路径）。

DB 访问：psycopg2 直查，助手照 admin_mcp._query_one/_execute 同款
（admin_bundles 已有跨路由 import 先例）；无 active 行返回代码内默认值。
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from backend.shared.logger import logger

# ── 代码内默认策略（无 active 行时的生效值，与迁移 119 列默认一致）──
DEFAULT_POLICY: dict[str, Any] = {
    "id": "",
    "name": "",
    "active": False,
    "mode": "adaptive",
    "top_k": 3,
    "max_rounds": 2,
    "cost_budget_milliunits": 50,
    "timeout_ms": 8000,
    "prompt_key": "router.tool_selector",
    "tenant_id": "default",
}

# 候选来源标记（模拟器展示用，如实反映解析路径）
_SOURCE_DOMAIN_REGISTRY = "domain_registry"
_SOURCE_PLANNER_VISIBLE = "planner_visible"


# ═══════════════════════════════════════════════════
# DB 访问（同步 psycopg2；与 admin_mcp 同款助手）
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
    from psycopg2.extras import RealDictCursor

    with _db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or {})
            return [dict(r) for r in cur.fetchall()]


def _query_one(sql: str, params: dict | None = None) -> dict | None:
    rows = _query_all(sql, params)
    return rows[0] if rows else None


def _execute(sql: str, params: dict | None = None) -> int:
    """执行写语句并返回受影响行数。"""
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            return cur.rowcount


def _policy_row_to_dict(row: dict) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "name": str(row.get("name") or ""),
        "active": bool(row.get("active")),
        "mode": str(row.get("mode") or DEFAULT_POLICY["mode"]),
        "top_k": int(row.get("top_k") or DEFAULT_POLICY["top_k"]),
        "max_rounds": int(row.get("max_rounds") or DEFAULT_POLICY["max_rounds"]),
        "cost_budget_milliunits": int(
            row.get("cost_budget_milliunits")
            or DEFAULT_POLICY["cost_budget_milliunits"]),
        "timeout_ms": int(row.get("timeout_ms") or DEFAULT_POLICY["timeout_ms"]),
        "prompt_key": str(row.get("prompt_key") or DEFAULT_POLICY["prompt_key"]),
        "tenant_id": str(row.get("tenant_id") or DEFAULT_POLICY["tenant_id"]),
        "created_by": str(row.get("created_by") or ""),
        "updated_by": str(row.get("updated_by") or ""),
        "created_at": str(row.get("created_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
    }


def load_active_policy(tenant_id: str) -> dict[str, Any]:
    """生效策略：active 行，缺失时回退代码内默认值（部署兼容）。"""
    try:
        row = _query_one(
            "SELECT * FROM ai.tool_discovery_policy "
            "WHERE tenant_id = %(tenant)s AND active "
            "ORDER BY updated_at DESC LIMIT 1",
            {"tenant": str(tenant_id or "default")})
    except Exception as e:  # noqa: BLE001 — 读侧软失败按默认值
        logger.warning(f"[Discovery] 策略读取失败（按默认值处理）: {e}")
        return dict(DEFAULT_POLICY, tenant_id=str(tenant_id or "default"))
    if row is None:
        return dict(DEFAULT_POLICY, tenant_id=str(tenant_id or "default"))
    return _policy_row_to_dict(row)


def load_policies(tenant_id: str) -> list[dict[str, Any]]:
    """全部策略行（管理端列表视图，含未 active 行）。"""
    rows = _query_all(
        "SELECT * FROM ai.tool_discovery_policy "
        "WHERE tenant_id = %(tenant)s "
        "ORDER BY active DESC, updated_at DESC",
        {"tenant": str(tenant_id or "default")})
    return [_policy_row_to_dict(r) for r in rows]


def upsert_policy(
    *,
    tenant_id: str,
    name: str,
    actor: str,
    mode: str = DEFAULT_POLICY["mode"],
    top_k: int = DEFAULT_POLICY["top_k"],
    max_rounds: int = DEFAULT_POLICY["max_rounds"],
    cost_budget_milliunits: int = DEFAULT_POLICY["cost_budget_milliunits"],
    timeout_ms: int = DEFAULT_POLICY["timeout_ms"],
    prompt_key: str = DEFAULT_POLICY["prompt_key"],
) -> dict[str, Any]:
    """按名称 upsert 并置为该租户唯一 active 行（幂等：同内容重写同 id）。"""
    tenant = str(tenant_id or "default")
    with _db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM ai.tool_discovery_policy "
                "WHERE name = %(name)s AND tenant_id = %(tenant)s",
                {"name": name, "tenant": tenant})
            row = cur.fetchone()
            if row is None:
                policy_id = f"disc_pol_{uuid.uuid4().hex[:12]}"
                cur.execute(
                    """
                    INSERT INTO ai.tool_discovery_policy
                        (id, name, active, mode, top_k, max_rounds,
                         cost_budget_milliunits, timeout_ms, prompt_key,
                         tenant_id, created_by, updated_by)
                    VALUES (%(id)s, %(name)s, true, %(mode)s, %(top_k)s,
                            %(max_rounds)s, %(cost_budget)s, %(timeout)s,
                            %(prompt_key)s, %(tenant)s, %(actor)s, %(actor)s)
                    """,
                    {"id": policy_id, "name": name, "mode": mode,
                     "top_k": top_k, "max_rounds": max_rounds,
                     "cost_budget": cost_budget_milliunits,
                     "timeout": timeout_ms, "prompt_key": prompt_key,
                     "tenant": tenant, "actor": actor})
            else:
                policy_id = str(row[0])
                cur.execute(
                    """
                    UPDATE ai.tool_discovery_policy
                    SET mode = %(mode)s, top_k = %(top_k)s,
                        max_rounds = %(max_rounds)s,
                        cost_budget_milliunits = %(cost_budget)s,
                        timeout_ms = %(timeout)s, prompt_key = %(prompt_key)s,
                        active = true, updated_by = %(actor)s,
                        updated_at = now()
                    WHERE id = %(id)s
                    """,
                    {"id": policy_id, "mode": mode, "top_k": top_k,
                     "max_rounds": max_rounds,
                     "cost_budget": cost_budget_milliunits,
                     "timeout": timeout_ms, "prompt_key": prompt_key,
                     "actor": actor})
            # 单 active 不变量：本租户其余行全部落 active=false
            cur.execute(
                "UPDATE ai.tool_discovery_policy SET active = false "
                "WHERE tenant_id = %(tenant)s AND id <> %(id)s AND active",
                {"tenant": tenant, "id": policy_id})
    return load_active_policy(tenant)


# ═══════════════════════════════════════════════════
# 候选解析（只读，复用既有原语）
# ═══════════════════════════════════════════════════

@contextmanager
def _request_identity(product_id: str, tenant_id: str) -> Iterator[None]:
    """把产品/租户身份注入 request_context（set/reset 包裹，测试同模式）。

    product_allows_capability 的动态 MCP / bundle 授权快照裁决经
    ContextVar 取租户；不注入时统一按 default 租户裁决会误判。
    """
    from backend.core.request_context import (
        _current_product_id,
        _current_product_source,
        _current_tenant_id,
    )

    product_token = _current_product_id.set(str(product_id or "internal"))
    source_token = _current_product_source.set("registered_route")
    tenant_token = _current_tenant_id.set(str(tenant_id or ""))
    try:
        yield
    finally:
        _current_tenant_id.reset(tenant_token)
        _current_product_source.reset(source_token)
        _current_product_id.reset(product_token)


def _raw_candidates(domain: str) -> tuple[list[Any], str]:
    """域候选解析：域已知走 domain registry，域未知回退 planner 可见全集。

    返回 (候选对象列表, 来源标记)。回退面包装成 name-only 候选，
    风险信息从 tool spec 补齐（UNKNOWN 兜底）。
    """
    from backend.orchestration.router.hierarchical import resolve_domain_tools
    from backend.orchestration.router.manifest import effective_manifest
    from backend.orchestration.router.hierarchical import ToolCandidate

    if str(domain or "").strip():
        return list(resolve_domain_tools(domain.strip())), _SOURCE_DOMAIN_REGISTRY

    manifest = effective_manifest()
    return (
        [ToolCandidate(name=name) for name in manifest.planner_visible_capability_names],
        _SOURCE_PLANNER_VISIBLE,
    )


def resolve_candidates(
    query: str,
    product_id: str,
    tenant_id: str,
    domain: str = "",
    top_k: int = DEFAULT_POLICY["top_k"],
) -> dict[str, Any]:
    """当前策略 + 权限下的候选解析视图（B1 模拟器口径，不进运行时）。

    - 候选 = 域 registry（域未知时 planner 可见全集）→ 服务端权限收窄
      （_authorized_candidates：角色/scope 空集 + 产品域禁行根）→ 前 top_k；
    - B1 只做候选解析，不做语义排序：候选顺序 = registry 声明序；
      query 为 B2 语义选型预留入参，本阶段不参与解析；
    - excluded_count = 权限/治理过滤剔除的候选数（模拟器逐项解释）。
    """
    top_k = max(1, int(top_k or DEFAULT_POLICY["top_k"]))
    policy = load_active_policy(tenant_id)
    raw, source = _raw_candidates(domain)
    raw_count = len(raw)

    from backend.orchestration.router.capability_router import CapabilityRouter

    with _request_identity(product_id, tenant_id):
        allowed = CapabilityRouter._authorized_candidates(
            raw,
            {"product_id": str(product_id or "internal"),
             "_verified_roles": (), "_verified_scopes": ()},
            effective_domain=str(domain or ""),
        )
    picked = allowed[:top_k]

    from backend.core.tool_governance.registry import get_tool_spec

    candidates = []
    for item in picked:
        spec = get_tool_spec(item.name)
        candidates.append({
            "name": item.name,
            "risk": str(getattr(item, "risk_level", "") or (spec.risk_level.value if spec else "") or "UNKNOWN"),
            "source": source,
        })
    return {
        "query": str(query or ""),
        "domain": str(domain or ""),
        "product_id": str(product_id or "internal"),
        "candidates": candidates,
        "policy": policy,
        "excluded_count": max(0, raw_count - len(allowed)),
        "candidate_count": raw_count,
        "source": source,
    }
