"""SQLite tracer fixtures — 解决 2d627d7 移除内存 deque 后的测试 fixture 兼容性。

背景：
    旧 fixture 直接操作 `trace_collector._records / _active / _timers / _span_seq / _listeners`
    等模块级属性。2d627d7 重构后，trace 数据直接写 SQLite（重启不丢），这些内存属性
    大多已移除或语义变化。本 fixture 用临时 SQLite + 替换全局单例的方式兼容新架构。

关键陷阱：业务模块用 `from backend.rag.tracer import trace_collector` 是模块级绑定，
monkeypatch 替换 `tracer_mod.trace_collector` 不会自动更新其他模块的本地引用。
本 fixture 自动 patch 所有已知 import `trace_collector` 的模块（见 _REBIND_MODULES）。

用法：
    from backend.tests.fixtures.sqlite_tracer import fresh_collector

    def test_xxx(fresh_collector):
        # fresh_collector 是当前测试隔离的 TraceCollector 实例
        ...
"""
from __future__ import annotations

import pytest


# 所有 `from backend.rag.tracer import trace_collector` 的业务模块。
# 加新业务模块时必须同步更新这里，否则测试用 fresh_collector 但业务模块仍用旧实例。
_REBIND_MODULES = (
    "backend.rag.indexing.indexer",
    "backend.rag.chain",
    "backend.app.api.routes.rag",
    "backend.app.api.routes.observability",
    "backend.orchestration.supervisor.scheduler",
)


@pytest.fixture
def fresh_collector(tmp_path, monkeypatch):
    """每个测试前：

    1. 注入临时 SQLite 作为 trace_store（测试结束自动清理）
    2. 替换全局 trace_collector 为新实例（避免污染其他测试）
    3. 同步 patch 所有业务模块的 trace_collector 引用
    4. 重置 contextvar 防止上一个测试的 _current_trace_var 残留
    """
    from backend.rag import trace_store as ts_mod
    from backend.rag import tracer as tracer_mod

    # 1. 临时 SQLite DB
    temp_db = tmp_path / "trace_test.db"
    store = ts_mod.TraceStore(str(temp_db))
    monkeypatch.setattr(ts_mod, "_trace_store", store)
    # trace_collector 内部 `from backend.rag.trace_store import get_trace_store`
    # 每次都从 ts_mod 模块读 _trace_store，所以 monkeypatch 有效

    # 2. 替换全局 collector 单例（避免上一个测试的 listener / 状态泄漏）
    new_collector = tracer_mod.TraceCollector()
    monkeypatch.setattr(tracer_mod, "trace_collector", new_collector)

    # 3. 同步 patch 所有业务模块的本地引用（关键！否则它们用旧的全局对象）
    import importlib
    for mod_name in _REBIND_MODULES:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue  # 模块可能在新架构里被删，跳过
        if hasattr(mod, "trace_collector"):
            monkeypatch.setattr(mod, "trace_collector", new_collector)

    # 4. 重置 contextvar（防止上一个测试 start() 但没 finish 的 trace 残留）
    try:
        tracer_mod._current_trace_var.set(None)
    except Exception:
        pass

    yield new_collector
