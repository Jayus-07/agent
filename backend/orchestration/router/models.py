"""Router 收口阶段的跨适配器决策模型。

这些模型只描述“判断结果”，不携带 Tool、Skill 或业务服务对象，确保
DomainRouter、CapabilityRouter 与 ExecutionModeResolver 可以独立测试、序列化，
也不会把执行职责重新塞回 Router 层。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal, TypedDict


class DomainDecision(TypedDict):
    """顶级业务域判断结果。"""

    domain: str
    subflow: str | None
    confidence: float
    source: str
    reasoning: str


class CapabilityDecision(TypedDict):
    """域内 capability 候选结果，不代表已经执行。"""

    domain: str
    capability: str | None
    candidates: list[dict]
    confidence: float
    source: str
    reasoning: str


ExecutionMode = Literal["direct", "workflow", "plan", "domain_graph", "general"]


@dataclass(frozen=True)
class ExecutionModeDecision:
    """统一执行方式判断。

    ``compat_route_mode`` 只用于保留旧的 ``clarify`` 入口语义，不把
    ``clarify`` 扩展进新的顶层执行模式枚举。
    """

    mode: ExecutionMode
    target: str | None = None
    confidence: float = 0.0
    reasoning: str = ""
    compat_route_mode: str | None = None

    def to_dict(self) -> dict:
        """返回可进入 LangGraph/checkpoint 的普通字典。"""

        return asdict(self)


__all__ = [
    "CapabilityDecision",
    "DomainDecision",
    "ExecutionModeDecision",
    "ExecutionMode",
]
