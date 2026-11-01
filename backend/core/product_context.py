"""服务端注册的产品请求上下文与会话命名空间。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProductContext:
    """由服务端路由选择的产品身份，不接受请求体或普通请求头覆盖。"""

    product_id: str
    source: str
    domain_hint: str
    session_prefix: str
    requires_identity: bool
    persist_memory: bool


AGENT_PRODUCT = ProductContext(
    product_id="agent",
    source="registered_route",
    domain_hint="main",
    # Existing main-agent Memory rows use the raw authenticated user/session IDs.
    # The dedicated /memory/agent route remains their sole owner, so retaining
    # these keys is the compatibility path for existing sessions and profiles.
    session_prefix="",
    requires_identity=True,
    persist_memory=True,
)
CUSTOMER_SERVICE_PRODUCT = ProductContext(
    product_id="customer_service",
    source="registered_route",
    domain_hint="customer_service",
    # Customer Service conversations have their own customer_service tables;
    # preserve their existing conversation IDs instead of changing the key.
    session_prefix="",
    requires_identity=True,
    persist_memory=True,
)
PUBLIC_PRODUCT = ProductContext(
    product_id="public",
    source="shared_route_default",
    domain_hint="",
    session_prefix="public::",
    requires_identity=False,
    persist_memory=False,
)
INTERNAL_PRODUCT = ProductContext(
    product_id="internal",
    source="internal_call",
    domain_hint="",
    session_prefix="",
    requires_identity=False,
    persist_memory=True,
)


def namespace_session_id(product: ProductContext, session_id: str) -> str:
    """按服务端产品命名空间隔离会话，同时保持客户端 ID 不透明。"""
    value = str(session_id or "default")
    prefix = product.session_prefix
    if prefix and not value.startswith(prefix):
        value = f"{prefix}{value}"
    if len(value) > 128:
        raise ValueError("会话标识过长")
    return value


def unnamespace_session_id(product: ProductContext, session_id: str) -> str:
    """把产品命名空间从会话 ID 中移除，供同一产品前端继续使用。"""
    prefix = product.session_prefix
    value = str(session_id or "")
    return value[len(prefix):] if prefix and value.startswith(prefix) else value


_PRODUCTS = {
    product.product_id: product
    for product in (
        AGENT_PRODUCT, CUSTOMER_SERVICE_PRODUCT, PUBLIC_PRODUCT, INTERNAL_PRODUCT,
    )
}


def product_by_id(product_id: str) -> ProductContext:
    """只解析服务端固定产品 ID；未知值按无能力的公共产品处理。"""
    return _PRODUCTS.get(product_id, PUBLIC_PRODUCT)


def memory_user_id(product: ProductContext, user_id: str) -> str:
    """返回产品 Memory owner key；Agent 保留旧键，客服画像单独命名空间。"""
    value = str(user_id or "").strip()
    if not value:
        raise ValueError("产品记忆需要可信用户身份")
    if not product.persist_memory:
        raise ValueError("该产品入口未启用持久记忆")
    if product.product_id == "internal":
        return value
    if product.product_id == "agent":
        # Agent owns the pre-product-scope Memory namespace. Keeping the raw
        # authenticated ID preserves existing sessions and long-term profiles;
        # Customer Service history is read from its separate conversation store.
        return value
    scoped = f"{product.product_id}::{value}"
    if len(scoped) > 128:
        import hashlib
        scoped = f"{product.product_id}::{hashlib.sha256(value.encode('utf-8')).hexdigest()}"
    return scoped


def namespaced_stream_id(
    product: ProductContext,
    request_id: str,
    user_id: str = "",
    tenant_id: str = "",
) -> str:
    """恢复注册表键同时隔离产品、可信租户/用户和请求 ID。"""
    value = str(request_id or "")
    import hashlib

    identity = hashlib.sha256(
        f"{tenant_id}\0{user_id}".encode("utf-8"),
    ).hexdigest()[:24]
    return f"{product.product_id}::{identity}::{value}"


def identity_scope_key(product: ProductContext, user_id: str, tenant_id: str) -> str:
    """生成不含身份明文的产品/租户/用户作用域键。"""
    import hashlib

    identity = hashlib.sha256(
        f"{tenant_id}\0{user_id}".encode("utf-8"),
    ).hexdigest()[:24]
    return f"{product.product_id}::{identity}"


def _dynamic_mcp_granted(product_id: str, capability: str) -> bool:
    """MCP 动态工具的产品授权裁决（ADR-0011 D5）。

    授权事实源是 ai.mcp_tool_grants 的进程内快照（product + capability +
    租户三元组）；授权系统不可用时 fail-closed。
    """
    try:
        from backend.core.request_context import get_tool_tenant_id
        from backend.infra.mcp_registry import store as _mcp_store

        return _mcp_store.product_granted(
            product_id, capability, get_tool_tenant_id())
    except Exception:  # noqa: BLE001 — 授权判定失败按拒绝
        return False


def _static_bundle_granted(product_id: str, capability: str) -> bool | None:
    """静态能力的包授权裁决（Batch 2）。

    事实源是 ai.product_bundle_grants 的进程内快照（bundle_registry）；
    返回 None = 快照未加载或库中无任何授权行（部署兼容：回退域黑名单）。
    """
    try:
        from backend.core.request_context import get_tool_tenant_id
        from backend.infra.bundle_registry import store as _bundle_store

        return _bundle_store.bundle_allows(
            product_id, capability, get_tool_tenant_id())
    except Exception:  # noqa: BLE001 — 授权判定失败按拒绝（不回退黑名单）
        return False


# ── 域专属禁行根（决策 #15 业务域硬隔离，运行门与授权端点共用）───────
# agent/customer_service 产品不可执行跨域专属能力；bundle 白名单只能在
# 边界内放宽非专属能力，不得越过本边界——「管理员授权」不构成跨域提权
# 依据（PR #115 审计修复，覆盖 Batch 2「白名单优先于黑名单」的旧语义）。
# 说明：cs 域静态能力历史命名为 cs.*（root=cs），customer_service 为
# 规范 root，两者都在 agent 禁行集合内。
# 2026-11-01 收编重构：commerce 能力根随原商务域图壳删除
# （平台已无 commerce.* 能力，hotel/flight 检索归 travel.*）。
_DOMAIN_EXCLUSIVE_ROOTS: dict[str, frozenset[str]] = {
    "agent": frozenset({
        "travel", "travel_booking",
        "customer_service", "cs", "selection_funnel",
        "booking"}),
    "customer_service": frozenset({
        "travel", "travel_booking",
        "selection_funnel", "booking"}),
}


def domain_exclusive_forbidden_roots(product_id: str) -> frozenset[str]:
    """产品入口的域专属禁行根集合（授权端点据此拒绝跨域误授权）。"""
    return _DOMAIN_EXCLUSIVE_ROOTS.get(str(product_id or ""), frozenset())


# bundle.domain 标签 → 该域的禁行根（bundle 授权端点校验动态成员时使用，
# 动态 mcp.* 工具名无域语义，bundle.domain 是其唯一域归属信号）。
_BUNDLE_DOMAIN_EXCLUSIVE_ROOTS: dict[str, frozenset[str]] = {
    "travel": frozenset({"travel"}),
    "cs": frozenset({"customer_service", "cs"}),
    "selection": frozenset({"selection_funnel", "booking"}),
}

# 管理端业务域标签词汇表（工具治理 governance.domain / 能力包 domain
# 共用；空值 = 未分类，不做域约束——兼容存量数据）。
DOMAIN_LABELS = frozenset({
    "general", "cs", "selection", "travel", "data",
    "knowledge", "business", "report", "communication", "mcp",
})


def product_allows_capability(
    product_id: str,
    capability: str,
    domains: tuple[str, ...] | list[str] = (),
) -> bool:
    """产品级能力上限；不替代 CapabilityRouter 或资源级授权。"""
    product = product_by_id(product_id)
    if product.product_id == "internal":
        return True
    if product.product_id == "public":
        return False
    name = str(capability or "").strip().lower()
    domain_values = {str(value or "").strip().lower() for value in domains}
    roots = {name.split(".", 1)[0]} | domain_values
    forbidden_roots = _DOMAIN_EXCLUSIVE_ROOTS.get(product.product_id, frozenset())
    # 动态 MCP 能力没有静态前缀语义，唯一放行依据是授权快照；
    # 工具名、domain_hint、Planner 输出都不构成授权（ADR-0002 决策 5）。
    if name.startswith("mcp."):
        return _dynamic_mcp_granted(product.product_id, name)
    # 静态能力：能力包授权表非空 → 白名单判定（Batch 2），但域专属禁行根
    # 始终生效——白名单只放宽边界内能力，不构成跨域提权（决策 #15）；
    # 表为空/未加载（None）→ 回退既有域黑名单（部署兼容）。
    bundle_verdict = _static_bundle_granted(product.product_id, name)
    if bundle_verdict is not None:
        return bool(bundle_verdict) and not bool(roots.intersection(forbidden_roots))
    if product.product_id == "agent":
        return not bool(roots.intersection(forbidden_roots))
    if product.product_id == "customer_service":
        return not bool(roots.intersection(forbidden_roots))
    return False


def product_id_from_state(state: dict | None) -> str:
    """图状态 → 可信产品 ID（图状态管道口径）。

    与执行期门（skills/base.py、direct_executor）同取值链：request_context
    投影优先，state 平铺键兜底，缺省 internal（内部调用/单测不经产品过滤；
    真实请求由 runner 从服务端注册路由写入，两处一致）。
    """
    ctx = (state or {}).get("request_context")
    product_id = (
        ctx.get("product_id")
        if isinstance(ctx, dict)
        else getattr(ctx, "product_id", None)
    ) or (state or {}).get("product_id") or "internal"
    return str(product_id or "internal")


def filter_capabilities_for_state(
    capabilities, state: dict | None,
) -> tuple[str, ...]:
    """按当前请求的产品/域收窄能力清单（Planner/Critique 渲染面专用）。

    与执行期门（skills/base.py、direct_executor、GovernanceRuntime）同谓词：
    product_allows_capability(product, cap, (state.domain,))——渲染面不得
    宽于执行面，否则未授权工具的描述/参数会被送进 LLM（PR #115 Codex P2）。
    动态 mcp.* 的租户裁决经 product_allows_capability 内部 ContextVar 取值，
    图内节点入口已由 RequestContext.bind() 绑定。
    """
    product_id = product_id_from_state(state)
    domain = str((state or {}).get("domain") or "")
    return tuple(
        str(name) for name in capabilities
        if product_allows_capability(
            product_id, str(name), (domain,) if domain else ())
    )


def product_allows_workflow(product_id: str, workflow_name: str) -> bool:
    """Workflow 执行点复核产品范围。"""
    product = product_by_id(product_id)
    if product.product_id == "internal":
        return True
    if product.product_id == "public":
        return False
    name = str(workflow_name or "").strip().lower()
    if product.product_id == "agent":
        return not any(token in name for token in ("travel", "customer_service", "selection", "booking"))
    if product.product_id == "customer_service":
        return not any(token in name for token in ("travel", "selection", "booking"))
    return False


def product_for_memory_path(path: str) -> ProductContext:
    """根据已注册的记忆 API 路径返回服务端产品身份。"""
    normalized = "/" + path.strip("/")
    if "/memory/agent/" in normalized or normalized.endswith("/memory/agent"):
        return AGENT_PRODUCT
    if "/memory/customer-service/" in normalized or normalized.endswith("/memory/customer-service"):
        return CUSTOMER_SERVICE_PRODUCT
    return PUBLIC_PRODUCT
