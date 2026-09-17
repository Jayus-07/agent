"""P0 地基：结构化分析层 — trace_summary（PG 实现）。

定位（增量升级路线图 P0）：
  - 本层在 finish() 时额外落一份「结构化摘要」，为后续阶段提供聚合查询地基：
      P1 Sessions   → sessions() GROUP BY session_id
      P2 Cost 面板  → cost_summary() 按模型/日聚合
      P4 评测数据集 → list(only_rejected=...) 真实流量提取
  - 2026-09-17 SQLite 轨已删除，唯一实现为 PostgresAnalyticsStore。

软失败原则（与 alerts / trace_store 一致）：
  - 写入/查询失败只记日志，绝不向上抛异常，不阻塞业务链路。

环境变量：
  OBS_ANALYTICS_ENABLED  true|false（默认 true；测试环境置 false 防污染）
"""

from __future__ import annotations

import json
import os
from typing import Any

from backend.shared.logger import logger

_MAX_ROWS = 20000  # 摘要行保留上限（聚合需要较长窗口）


def _cfg_enabled() -> bool:
    return os.getenv("OBS_ANALYTICS_ENABLED", "true").strip().lower() not in (
        "0", "false", "no", "off",
    )


class AnalyticsStore:
    """trace_summary 结构化存储接口（唯一实现：PostgresAnalyticsStore）。"""

    def __new__(cls, *args, **kwargs):
        # 2026-09-17 SQLite 轨删除：无条件返回 PG 实现（db_path 等参数兼容保留，表名由 env 决定）。
        from backend.observability.analytics_store_pg import PostgresAnalyticsStore
        return super().__new__(PostgresAnalyticsStore)

    @property
    def enabled(self) -> bool:
        return _cfg_enabled()

    def save(self, record: Any) -> bool:
        """从 TraceRecord 抽取结构化字段写入。失败只记日志，返回 False。"""
        if not self.enabled:
            return False
        try:
            data = self._record_to_dict(record)
            return self.save_dict(data)
        except Exception as e:
            logger.warning(f"[AnalyticsStore] 写入失败 "
                           f"{getattr(record, 'id', '?')}: {e}")
            return False

    @staticmethod
    def _record_to_dict(record: Any) -> dict:
        """TraceRecord → dict（跳过 _ 前缀内部属性）。"""
        d = {}
        for k, v in record.__dict__.items():
            if k.startswith("_"):
                continue
            if isinstance(v, dict):
                d[k] = {dk: dv for dk, dv in v.items()
                        if not (isinstance(dk, str) and dk.startswith("_"))}
            elif isinstance(v, list):
                d[k] = [AnalyticsStore._record_to_dict(x) if hasattr(x, "__dict__") else x
                        for x in v]
            elif hasattr(v, "__dict__"):
                d[k] = AnalyticsStore._record_to_dict(v)
            else:
                d[k] = v
        return d

    @staticmethod
    def _row_to_dict(r) -> dict:
        d = dict(r)
        d["id"] = d.pop("trace_id")
        d["timestamp"] = d.pop("ts")
        d["usage"] = {
            "prompt_tokens": d.pop("prompt_tokens"),
            "completion_tokens": d.pop("completion_tokens"),
            "total_tokens": d.pop("total_tokens"),
        }
        try:
            d["tags"] = json.loads(d.get("tags") or "{}")
        except Exception:
            d["tags"] = {}
        d["rejected"] = bool(d.get("rejected"))
        return d


# 模块级单例
_analytics_store: AnalyticsStore | None = None


def get_analytics_store() -> AnalyticsStore:
    """存储工厂（2026-09-17 SQLite 轨删除，直连 PG 实现）。"""
    global _analytics_store
    if _analytics_store is None:
        from backend.observability.analytics_store_pg import PostgresAnalyticsStore
        _analytics_store = PostgresAnalyticsStore()
    return _analytics_store
