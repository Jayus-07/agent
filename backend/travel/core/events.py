"""travel/core/events.py — 旅游域统一事件出口（v3 §2 骨架）

Phase 1 仅提供结构化 logger 出口；quality_metrics 的 Prometheus 面在
Phase 3 统一收编（届时各专家散落的 qm.record_* 改经此处）。事件字段一律
JSON 可序列化（default=str 兜底），禁止把运行时对象塞进日志上下文。
"""
from __future__ import annotations

import json
from typing import Any

from backend.shared.logger import logger

EVENT_SOURCE = "travel"


def build_travel_event(event: str, agent: str = "", **fields: Any) -> dict:
    """构造事件 dict（纯函数，可单测）。event/agent 为必填语义字段。"""
    return {"source": EVENT_SOURCE, "event": event, "agent": agent, **fields}


def emit_travel_event(event: str, agent: str = "", **fields: Any) -> dict:
    """构造并落日志；返回事件 dict 便于调用方复用/测试断言。"""
    payload = build_travel_event(event, agent=agent, **fields)
    logger.info("travel_event: %s", json.dumps(payload, ensure_ascii=False, default=str))
    return payload
