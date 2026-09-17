"""Tracer 测试 fixtures — PG 唯一实现时代的 trace 隔离。

背景：
    旧 fixture 直接操作 `trace_collector._records / _active / _timers / _span_seq / _listeners`
    等模块级属性。2d627d7 重构后 trace 数据直接落库；2026-09-17 SQLite 轨删除后，
    TraceStore 唯一实现为 PostgresTraceStore。本 fixture 用 `pgtest_biz_` 前缀表
    （backend/tests/fixtures/pg_env.py 的机制）替换全局单例实现隔离。

关键陷阱：业务模块用 `from backend.observability.tracer import trace_collector` 是模块级绑定，
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

from backend.tests.fixtures.pg_env import (  # noqa: F401
    drop_pgtest_tables,
    set_pg_test_env,
    _pop_store_modules,
)


# 所有 `from backend.observability.tracer import trace_collector` 的业务模块。
# 加新业务模块时必须同步更新这里，否则测试用 fresh_collector 但业务模块仍用旧实例。
_REBIND_MODULES = (
    "backend.rag.indexing.indexer",
    "backend.rag.chain",
    "backend.app.api.routes.rag",
    "backend.app.api.routes.observability",
    "backend.orchestration.supervisor.scheduler",
)


@pytest.fixture
def fresh_collector(request, monkeypatch):
    """每个测试前：

    1. 注入 pgtest 前缀 PG trace store（用例前后清表，结束自动清理）
    2. 替换全局 trace_collector 为新实例（避免污染其他测试）
    3. 同步 patch 所有业务模块的 trace_collector 引用
    4. 重置 contextvar 防止上一个测试的 _current_trace_var 残留
    5. 同步化 trace 写入（消除异步 worker 竞态：测试在 finish() 后立即 list()）
    """
    from backend.observability import trace_store as ts_mod
    from backend.observability import tracer as tracer_mod

    # 1. PG 测试表 env + 建表（TraceStore() → PostgresTraceStore，__init__ 建表）
    set_pg_test_env(monkeypatch)
    drop_pgtest_tables()
    request.addfinalizer(_pop_store_modules)  # teardown: 让后续 import 回到生产表名
    store = ts_mod.TraceStore()
    monkeypatch.setattr(ts_mod, "_trace_store", store)
    # trace_collector 内部 `from backend.observability.trace_store import get_trace_store`
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

    # 5. 同步化 trace 写入（消除异步 worker 竞态）
    from backend.observability import trace_writer as tw_mod
    _orig_enqueue = tw_mod.TraceWriteQueue.enqueue

    def _sync_enqueue(self_queue, record):
        data = tw_mod._serialize_record(record)
        stores = self_queue._capture_stores()
        trace_store = stores[0]
        try:
            trace_store.save_dict(data)
        except Exception:
            pass

    monkeypatch.setattr(tw_mod.TraceWriteQueue, "enqueue", _sync_enqueue)

    # 注：Redis 通道的隔离由 conftest 的 _trace_writer_local_only 统一负责
    # （全局禁用 _use_redis + 每条用例前排空本地队列），此处无需重复处理。

    yield new_collector

    drop_pgtest_tables()
