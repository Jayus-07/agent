"""
backend/infra/llm/gateway/base.py — 动态运行时探针与代理穿透辅助
"""
from __future__ import annotations

import sys
from typing import Any


def get_proxy_attr(name: str, default: Any = None) -> Any:
    """动态获取 proxy.py 模块属性，优先穿透读取 monkeypatch 运行时覆盖。"""
    mod = sys.modules.get("backend.infra.llm.proxy")
    if mod is not None and hasattr(mod, name):
        return getattr(mod, name)
    return default
