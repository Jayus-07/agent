"""LLM 用量明细存储 — llm_usage 表（每次 LLM 调用一行，PG 实现）。

定位（Token 统计看板的数据地基）：
  - proxy._record_tokens 每次成功调用后写入一行，模型/成本按实际模型计价
  - 看板聚合（总量/日趋势/按模型）直接在本表 GROUP BY，与 trace 聚合解耦：
    避免 agent 根 trace 与嵌套 RAG 子 trace 的 token 双计问题
  - trace_id/session_id 用于按轮次（per-turn）追溯
  - 2026-09-17 SQLite 轨已删除，唯一实现为 PostgresLLMUsageStore。

软失败原则（与 analytics_store 一致）：
  写入/查询失败只记日志，绝不向上抛异常，不阻塞 LLM 主链路。
"""

from __future__ import annotations

import os
import threading
import time

_MAX_ROWS = 500_000  # 明细行保留上限（每次调用一行，量大；超限删最旧）


def _now_iso() -> str:
    """ISO8601 UTC（与 tracer._now_iso 同格式：YYYY-MM-DDTHH:MM:SS.mmmZ）。"""
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 1000):03d}Z"


def _cfg_enabled() -> bool:
    """与 analytics_store 同一开关：测试环境可整体关闭防污染。"""
    return os.getenv("OBS_ANALYTICS_ENABLED", "true").strip().lower() not in (
        "0", "false", "no", "off",
    )


def current_usage_attribution() -> dict[str, str]:
    """读取当前请求的权威用量归属，不接受调用参数中的自报身份。"""
    try:
        from backend.core.request_context import (
            get_tool_tenant_id,
            get_tool_user_id,
        )
        user_id = get_tool_user_id() or ""
        tenant_id = get_tool_tenant_id() or ""
    except Exception:
        user_id = ""
        tenant_id = ""

    trace_id = ""
    session_id = ""
    request_id = ""
    try:
        from backend.observability.tracer import trace_collector

        trace = trace_collector.current()
        if trace is not None:
            trace_id = str(getattr(trace, "id", "") or "")
            session_id = str(getattr(trace, "session_id", "") or "")
            request_id = str(getattr(trace, "request_id", "") or trace_id)
    except Exception:
        pass
    return {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "trace_id": trace_id,
        "session_id": session_id,
        "request_id": request_id,
    }


class LLMUsageStore:
    """llm_usage 明细存储接口（唯一实现：PostgresLLMUsageStore）。"""

    def __new__(cls, *args, **kwargs):
        # 2026-09-17 SQLite 轨删除：无条件返回 PG 实现（db_path 等参数兼容保留，表名由 env 决定）。
        from backend.observability.llm_usage_store_pg import PostgresLLMUsageStore
        return super().__new__(PostgresLLMUsageStore)

    @staticmethod
    def _cutoff_iso(days: int) -> str:
        """N 天前（含当天）的 UTC 零点，ISO 格式；llm_usage.ts 为 ISO 字典序可比。"""
        return time.strftime("%Y-%m-%dT00:00:00", time.gmtime(time.time() - (days - 1) * 86400))


# 模块级单例
_llm_usage_store: LLMUsageStore | None = None
_store_lock = threading.Lock()


def get_llm_usage_store() -> LLMUsageStore:
    """存储工厂（2026-09-17 SQLite 轨删除，直连 PG 实现）。"""
    global _llm_usage_store
    if _llm_usage_store is None:
        with _store_lock:
            if _llm_usage_store is None:
                from backend.observability.llm_usage_store_pg import PostgresLLMUsageStore
                _llm_usage_store = PostgresLLMUsageStore()
    return _llm_usage_store
