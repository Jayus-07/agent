"""旧 Router API 的兼容门面。

路由决策只有一个生产实现：:class:`RoutingEngine`。
本模块保留 ``Router`` 和 ``get_router``，是为了给外部调用方一个平滑迁移
入口；这里不再承载 legacy、shadow 或第二套路由算法。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from backend.orchestration.router.engine import (
    RoutingEngine,
    get_routing_engine,
)
from backend.orchestration.router.types import RouteDecision


class Router:
    """RoutingEngine 的兼容门面，不包含任何路由业务逻辑。"""

    def __init__(
        self,
        llm_timeout: int | None = None,
        *,
        engine: RoutingEngine | None = None,
    ) -> None:
        # 保留旧参数，避免第三方调用方在迁移期间直接崩溃。
        del llm_timeout
        self.engine = engine or get_routing_engine()

    def route(
        self,
        query: str,
        context: Mapping[str, Any] | None = None,
    ) -> RouteDecision:
        """同步兼容入口，统一转发到 RoutingEngine。"""

        return self.engine.route(query, dict(context or {}))

_router: Router | None = None


def get_router() -> Router:
    """返回唯一生产路由引擎的兼容门面。"""

    global _router
    if _router is None:
        _router = Router()
    return _router


__all__ = ["Router", "get_router"]
