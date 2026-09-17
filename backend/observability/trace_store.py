"""Trace 持久化存储 — 接口层（PG 实现）。

TraceCollector 内存最多 200 条，重启即丢失。此模块定义 TraceStore
接口与共享序列化工具；唯一实现为 PostgresTraceStore（trace_store_pg.py，
rag 迁移 2026-09-17 后 SQLite 轨已删除，工厂直连 PG）。
"""

from __future__ import annotations

from typing import Any

_MAX_ROWS = 5000  # 最多保留条数（PG 实现定期清理旧数据的上限）


def _serialize_trace(trace: Any) -> dict:
    """TraceRecord → JSON 可序列化的 dict。跳过 _ 前缀内部属性
    （如 Span._t0 计时器，非数据字段，序列化会带出噪音）。"""
    if hasattr(trace, "__dict__"):
        d = {}
        for k, v in trace.__dict__.items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict):
                d[k] = {dk: dv for dk, dv in v.items()
                        if not (isinstance(dk, str) and dk.startswith("_"))}
            elif isinstance(v, list):
                d[k] = [_serialize_trace(x) if hasattr(x, "__dict__") else x for x in v]
            elif hasattr(v, "__dict__"):
                d[k] = _serialize_trace(v)
            else:
                d[k] = v
        return d
    return trace


class TraceStore:
    """trace 持久化存储接口（唯一实现：PostgresTraceStore）。"""

    def __new__(cls, *args, **kwargs):
        # 2026-09-17 SQLite 轨删除：无条件返回 PG 实现（db_path 等参数兼容保留，表名由 env 决定）。
        from backend.observability.trace_store_pg import PostgresTraceStore
        return super().__new__(PostgresTraceStore)

    pass


# 模块级单例
_trace_store: TraceStore | None = None


def get_trace_store() -> TraceStore:
    """存储工厂（2026-09-17 SQLite 轨删除，直连 PG 实现）。"""
    global _trace_store
    if _trace_store is None:
        from backend.observability.trace_store_pg import PostgresTraceStore
        _trace_store = PostgresTraceStore()
    return _trace_store
