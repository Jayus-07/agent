"""CS Admin 领域子路由共享基础、常量与动态门面代理。"""
from __future__ import annotations

import logging
import sys
from typing import Any

logger = logging.getLogger("rag_system")

_CS_OPERATOR_ROLES: frozenset[str] = frozenset({"admin", "super_admin", "supervisor", "agent"})


class FacadeProxy:
    """透明门面代理：
    优先从门面模块 (backend.app.api.routes.cs_admin) 解析被 monkeypatch 替换的属性，
    如果未被 patch 或门面未就绪，则回退到本地默认实现。
    """
    def __init__(self, facade_module: str) -> None:
        self._facade_module = facade_module
        self._defaults: dict[str, Any] = {}

    def register(self, name: str, default_val: Any) -> None:
        self._defaults[name] = default_val

    def __getattr__(self, name: str) -> Any:
        mod = sys.modules.get(self._facade_module)
        default_val = self._defaults.get(name)
        if mod is not None and hasattr(mod, name):
            val = getattr(mod, name)
            if default_val is None or val is not default_val:
                return val
        if default_val is not None:
            return default_val
        if mod is not None and hasattr(mod, name):
            return getattr(mod, name)
        raise AttributeError(f"'{self._facade_module}' has no attribute '{name}'")


facade = FacadeProxy("backend.app.api.routes.cs_admin")

__all__ = ["logger", "_CS_OPERATOR_ROLES", "FacadeProxy", "facade"]
