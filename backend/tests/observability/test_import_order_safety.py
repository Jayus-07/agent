# -*- coding: utf-8 -*-
"""test_import_order_safety.py — 循环导入回归（2026-10-08 实机崩溃）。

事故：error_taxonomy 顶层 import tool_runtime.models（触发父包 __init__
→ executor → tracing），tracing 顶层又 import error_taxonomy —— app 启动
恰先导 error_taxonomy 时以 partially initialized 炸 ImportError，
agent-app-1 Restarting 崩溃循环。

锁定不变量：**先导入 error_taxonomy 再导入 tool_runtime.tracing 必须成功**
（与 app 启动顺序一致）。回归若复现，tracing 顶部的 error_taxonomy import
就是回归点。
"""

from __future__ import annotations

import importlib
import sys


def test_error_taxonomy_first_then_tracing():
    """复刻 app 启动导入顺序：error_taxonomy 先于 tool_runtime.tracing。"""
    for mod in (
        "backend.observability.error_taxonomy",
        "backend.core.tool_runtime",
        "backend.core.tool_runtime.tracing",
    ):
        sys.modules.pop(mod, None)

    et = importlib.import_module("backend.observability.error_taxonomy")
    assert hasattr(et, "unify_tool_status")
    tr = importlib.import_module("backend.core.tool_runtime.tracing")
    assert callable(tr.finish_tool_span)


def test_reverse_order_also_works():
    """反向顺序（先 tool_runtime 后 error_taxonomy）同样必须成功。"""
    for mod in (
        "backend.observability.error_taxonomy",
        "backend.core.tool_runtime",
        "backend.core.tool_runtime.tracing",
    ):
        sys.modules.pop(mod, None)

    tr = importlib.import_module("backend.core.tool_runtime.tracing")
    et = importlib.import_module("backend.observability.error_taxonomy")
    assert callable(tr.finish_tool_span)
    assert callable(et.unify_tool_status)
