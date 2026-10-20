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
    forbidden = {"travel", "travel_booking", "travel_commerce", "customer_service", "selection_funnel", "booking", "commerce"}
    if product.product_id == "agent":
        return not bool(roots.intersection(forbidden))
    if product.product_id == "customer_service":
        return not bool(roots.intersection({"travel", "travel_booking", "travel_commerce", "selection_funnel", "booking", "commerce"}))
    return False


def product_allows_workflow(product_id: str, workflow_name: str) -> bool:
    """Workflow 执行点复核产品范围。"""
    product = product_by_id(product_id)
    if product.product_id == "internal":
        return True
    if product.product_id == "public":
        return False
    name = str(workflow_name or "").strip().lower()
    if product.product_id == "agent":
        return not any(token in name for token in ("travel", "customer_service", "selection", "booking", "commerce"))
    if product.product_id == "customer_service":
        return not any(token in name for token in ("travel", "selection", "booking", "commerce"))
    return False


def product_for_memory_path(path: str) -> ProductContext:
    """根据已注册的记忆 API 路径返回服务端产品身份。"""
    normalized = "/" + path.strip("/")
    if "/memory/agent/" in normalized or normalized.endswith("/memory/agent"):
        return AGENT_PRODUCT
    if "/memory/customer-service/" in normalized or normalized.endswith("/memory/customer-service"):
        return CUSTOMER_SERVICE_PRODUCT
    return PUBLIC_PRODUCT
