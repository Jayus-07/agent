"""Trace 资源级授权与租户隔离（管理端/API 主题，2026-10-10）。

背景与既有 P0：`/observability/traces` 与 `/observability/traces/{id}` 此前
**完全没有后端授权**——router 与端点均无 `Depends`，网关 `ROLE_GATE_PREFIXES`
也不覆盖 `/api/observability/traces` 前缀。因此任何通过网关验签的 viewer
用户都能读取完整 Trace，而 Trace 含 prompt/answer/用户问题/session/user 标识
且默认 `TRACE_DETAIL_LEVEL=full` 不做脱敏。

本模块只做两件事，均**复用既有机制**、不新建平行实现：
  1. `require_trace_admin`：直接复用 `deps.py::require_admin_user`（该类端点
     的既有语义），使后端直连与网关请求受同一道闸保护——不依赖网关。
  2. `trace_visible_to`：按 Trace 归属标签做资源级判定。归属来自
     `tracer.py` 在 `start()` 时从**权威请求上下文**写入的
     `tags["tenant_id"]` / `tags["user_id"]`，不消费客户端自报字段。

租户语义遵循 `identity.py` 的既定约束：空 tenant_id 表示「未声明」，
**不得降级为共享租户**——因此未声明归属的 Trace 对非超管一律不可见。
平台管理员同样遵守数据范围：只有 `super_admin` 可跨租户，admin 仅限本租户。
"""
from __future__ import annotations

from typing import Any, Mapping

from fastapi import Request

from backend.app.api.deps import (  # noqa: F401  (re-export 供路由统一挂载)
    require_admin_user,
)
from backend.shared.logger import logger

# 跨租户读取仅限平台超管；admin 仍限本租户。
CROSS_TENANT_ROLES = frozenset({"super_admin"})


def _trace_tags(data: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """取出 Trace 的归属标签；兼容 dict 与已反序列化对象两种形态。"""
    if not data:
        return {}
    if isinstance(data, Mapping):
        tags = data.get("tags")
    else:
        tags = getattr(data, "tags", None)
    return tags if isinstance(tags, Mapping) else {}


def _identity_tenant(request: Request) -> tuple[str, str]:
    """返回 (user_id, tenant_id)；均来自网关验签注入的权威身份。"""
    try:
        from backend.app.api.identity import resolve_identity

        ident = resolve_identity(request)
        return ident.user_id or "", ident.tenant_id or ""
    except Exception:
        logger.debug("[TraceAuthz] 身份解析失败", exc_info=True)
        return "", ""


def trace_visible_to(
    request: Request,
    data: Mapping[str, Any] | None,
    *,
    role: str = "",
) -> bool:
    """判定当前请求是否有权读取该 Trace（资源级 + 租户级）。

    规则（从严）：
      - Trace 未声明 tenant_id：仅 super_admin 可读。空租户不得降级为共享，
        否则未归属数据会对所有登录用户敞开。
      - 请求方 tenant_id 为空且不是 super_admin：一律拒绝（无法证明归属）。
      - tenant 不匹配：仅 super_admin 可跨租户；admin 亦不得跨租户。
      - tenant 匹配且 Trace 声明了 user_id：本人或平台管理员可读。
    """
    tags = _trace_tags(data)
    trace_tenant = str(tags.get("tenant_id") or "").strip()
    trace_user = str(tags.get("user_id") or "").strip()
    user_id, req_tenant = _identity_tenant(request)

    is_super = role in CROSS_TENANT_ROLES
    is_admin = role in {"admin", "super_admin"}

    if not trace_tenant:
        # 未声明归属：不得当作公共数据。
        return is_super
    if not req_tenant:
        return is_super
    if trace_tenant != req_tenant:
        return is_super
    # 同租户内：声明了 user_id 时按主体收敛；未声明则本租户管理员可读。
    if trace_user:
        return is_super or is_admin or (bool(user_id) and user_id == trace_user)
    return is_super or is_admin


async def require_trace_viewer(request: Request):
    """Trace 读取的统一身份闸：必须是 JWT 用户（kind == "user"）。

    直接复用既有 `require_user_actor`：API Key / 内部令牌属服务间凭据，
    不映射用户身份，无法表达租户与资源归属，因此不参与 Trace 读取。
    返回 OperatorIdentity，供调用方继续做资源级判定。
    """
    from backend.app.api.deps import require_user_actor

    return await require_user_actor(request)


# 子 Trace 递归上限：父 → 子 → 孙三级足够覆盖现有 agent→rag/sql 链路，
# 同时避免恶意/异常父子关系导致的无界遍历与放大查询。
MAX_CHILD_DEPTH = 3
MAX_CHILDREN_PER_NODE = 50


def collect_authorized_children(
    request: Request,
    store,
    root_id: str,
    *,
    role: str = "",
    depth: int = 1,
    seen: set[str] | None = None,
) -> list[dict]:
    """收集 root 的子孙 Trace，**每个节点都独立执行资源级授权**。

    安全要点（对应本主题第 3 条要求）：
      - 不能因为可读父 Trace 就默认其子 ID 可读：每个子 Trace 逐条过
        `trace_visible_to`，无权者直接不进结果，也不作为继续下钻的跳板。
      - 循环关系：以 `seen` 去重，父子互指/自环不会死循环。
      - 递归上限：`MAX_CHILD_DEPTH` 封顶，避免深层放大查询。
      - 部分失败：store 异常在 store 层已软失败返回 []，不影响父 Trace 返回。
    """
    if depth > MAX_CHILD_DEPTH or not root_id:
        return []
    if seen is None:
        seen = {root_id}
    _, tenant_id = _identity_tenant(request)
    # 未声明租户 → 不具备任何租户作用域，不下钻（不得无作用域查询）。
    is_super = role in CROSS_TENANT_ROLES
    if not tenant_id and not is_super:
        return []
    try:
        raw_children = store.list_children(
            root_id, limit=MAX_CHILDREN_PER_NODE,
            tenant_id=tenant_id or None,
        )
    except Exception:
        logger.debug("[TraceAuthz] 子 Trace 查询失败: %s", root_id, exc_info=True)
        return []

    authorized: list[dict] = []
    for child in raw_children or []:
        child_id = str((child or {}).get("id") or (child or {}).get("trace_id") or "")
        if not child_id or child_id in seen:
            continue
        # 逐条资源级授权：无权子 Trace 既不返回，也不继续下钻。
        if not trace_visible_to(request, child, role=role):
            continue
        seen.add(child_id)
        authorized.append(child)
        deeper = collect_authorized_children(
            request, store, child_id, role=role,
            depth=depth + 1, seen=seen,
        )
        authorized.extend(deeper)
    return authorized
