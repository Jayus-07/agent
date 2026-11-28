"""RBAC 门面透明动态代理模块。

支持测试环境对 backend.app.api.routes.rbac 门面上的私有函数或基础设施对象（如 _db, _session_service）
进行 monkeypatch 时，内部子模块能够无感穿透调用。
"""
from __future__ import annotations

import sys
from typing import Any, Callable

FACADE_MODULE_NAME = "backend.app.api.routes.rbac"


def get_facade_attr(attr_name: str, fallback: Any = None) -> Any:
    mod = sys.modules.get(FACADE_MODULE_NAME)
    if mod is not None and hasattr(mod, attr_name):
        return getattr(mod, attr_name)
    return fallback


class DynamicDbProxy:
    """透明代理 _db 上下文管理器，当门面上的 _db 被 monkeypatch 时动态使用替换后的对象。"""
    def __init__(self, fallback_db: Any):
        object.__setattr__(self, "_fallback_db", fallback_db)

    def __call__(self, *args, **kwargs):
        active_db = get_facade_attr("_db", self._fallback_db)
        if active_db is self:
            active_db = self._fallback_db
        return active_db(*args, **kwargs)


class DynamicSessionServiceProxy:
    """透明代理 _session_service，当门面上的 _session_service 被 monkeypatch 时动态使用替换后的对象。"""
    def __init__(self, fallback_service: Any):
        object.__setattr__(self, "_fallback_service", fallback_service)

    def __getattr__(self, name: str) -> Any:
        active_svc = get_facade_attr("_session_service", self._fallback_service)
        if active_svc is self:
            active_svc = self._fallback_service
        return getattr(active_svc, name)


def current_func(func_name: str, default_func: Callable) -> Callable:
    """获取当前激活的函数（若门面上被 monkeypatch 则返回门面上的版本）。"""
    return get_facade_attr(func_name, default_func)
