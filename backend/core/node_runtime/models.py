"""node_runtime/models.py — NodeResult 通用生命周期结果

公共结果契约（docs/architecture/ai-runtime.md「Node Runtime」）：
仅允许 status / error / duration_ms / data 四个字段，业务字段
（CS 的 response_draft/evidence、Travel 的 data/notes 语义）禁入——
域结果契约（ExpertResult / TravelExpertResult）各自保留在 experts/base.py；
泛化模型渗入业务字段 = 假复用回潮。

status 语义：runner 自产状态见 NodeStatus；fn 正常返回时其自报 status
（result.get("status")，缺省 "success"）原样透传，供域 hooks/适配层消费
（与迁移前两个手写实现同语义）。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class NodeStatus(str, Enum):
    """runner 自身产出的状态值（fn 自报状态按原样透传，不受此枚举约束）。"""

    SUCCESS = "success"
    FAILED = "failed"      # SWALLOW_TO_STATUS 捕获异常后的归一化状态
    TIMEOUT = "timeout"    # THREAD_ISOLATED 限时窗口超时


@dataclass(frozen=True)
class NodeResult:
    """节点公共执行结果（通用四字段，禁止增加业务字段）。"""

    status: str
    error: str | None = None
    duration_ms: int = 0
    data: Any = None
