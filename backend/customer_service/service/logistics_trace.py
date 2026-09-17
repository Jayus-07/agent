"""customer_service/service/logistics_trace.py — 物流轨迹 Provider

SB-4：物流轨迹抽象 + 演示 Mock（可注入失败），预留真实物流 API 接入位。

分层设计：
- ``LogisticsTraceProvider`` 协议：未来对接真实物流商 API 时实现同一接口，
  ``get_trace_provider()`` 工厂替换返回值即可，LogisticsService 无需改动。
- ``MockTraceProvider``：内置 DEMO- 前缀订单的演示轨迹；支持通过
  ``CS_DEMO_FAULTS`` 注入故障（超时/报错/空结果/人为延迟），用于演示
  工具失败重试与降级路径。

故障注入仅演示模式生效；真实模式工厂恒返回 None，LogisticsService
回退到原有的订单状态推导摘要。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol

from backend.customer_service.service.demo_mode import is_demo_mode
from backend.shared.logger import logger


class TraceProviderError(Exception):
    """物流轨迹 Provider 异常（超时/上游报错等）。"""


@dataclass
class TraceEvent:
    """单条物流轨迹事件。"""

    time: str  # ISO 8601
    location: str
    description: str


@dataclass
class TraceResult:
    """轨迹查询结果。"""

    order_no: str
    provider: str
    events: list[TraceEvent] = field(default_factory=list)

    @property
    def latest_event(self) -> TraceEvent | None:
        return self.events[-1] if self.events else None

    def stale_days(self, now: datetime | None = None) -> int:
        """最新一条轨迹距今停滞的天数（无轨迹返回 -1）。"""
        latest = self.latest_event
        if latest is None:
            return -1
        try:
            last_time = datetime.fromisoformat(latest.time)
        except ValueError:
            return -1
        now = now or datetime.now(timezone.utc)
        if last_time.tzinfo is None:
            last_time = last_time.replace(tzinfo=timezone.utc)
        return max(0, (now - last_time).days)


class LogisticsTraceProvider(Protocol):
    """物流轨迹 Provider 协议 — 真实物流 API 接入时实现此接口。"""

    def get_trace(self, order_no: str) -> TraceResult:
        """按订单号查询物流轨迹。失败抛 TraceProviderError。"""
        ...


# =============================================
# 演示轨迹数据（与 demo_sandbox.sql 的 DEMO 订单对应）
# DEMO-1003 固定「最后更新 5 天前」→ 供物流延误/催件场景演示
# =============================================
def _ts(days_ago: int, hour: int = 10, minute: int = 0) -> str:
    t = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return t.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()


_DEMO_TRACES: dict[str, list[TraceEvent]] = {
    "DEMO-1001": [
        TraceEvent(_ts(6), "深圳分拣中心", "包裹已发出"),
        TraceEvent(_ts(5, 14), "深圳宝安国际机场", "航班起飞，运往目的地国"),
        TraceEvent(_ts(3), "美国洛杉矶清关中心", "清关完成"),
        TraceEvent(_ts(2, 16), "洛杉矶配送站", "派送中"),
        TraceEvent(_ts(1, 11), "美国洛杉矶", "已签收，签收人：本人"),
    ],
    "DEMO-1002": [
        TraceEvent(_ts(8), "广州分拣中心", "包裹已发出"),
        TraceEvent(_ts(6, 15), "英国伦敦清关中心", "清关完成"),
        TraceEvent(_ts(4, 13), "伦敦配送站", "派送中"),
        TraceEvent(_ts(3, 10), "英国伦敦", "已签收，签收人：前台代收"),
    ],
    "DEMO-1003": [
        TraceEvent(_ts(7), "深圳分拣中心", "包裹已发出"),
        TraceEvent(_ts(6, 18), "杭州转运中心", "到达转运中心"),
        TraceEvent(_ts(5, 9), "杭州转运中心", "发往目的地国"),  # 之后 5 天无更新 → 延误剧本
    ],
    "DEMO-1010": [
        TraceEvent(_ts(4), "上海分拣中心", "包裹已发出"),
        TraceEvent(_ts(3, 12), "上海浦东国际机场", "航班起飞，运往目的地国"),
    ],
}


class MockTraceProvider:
    """演示用轨迹 Provider。

    Args:
        traces: 订单号 → 轨迹事件（缺省用内置演示数据；
            未命中的订单号按空轨迹返回，由调用方回退状态推导）。
        faults: 故障注入开关（仅 demo 模式传入，见模块 docstring）。
    """

    provider_name = "mock"

    def __init__(
        self,
        traces: dict[str, list[TraceEvent]] | None = None,
        faults: dict | None = None,
    ) -> None:
        self._traces = traces if traces is not None else _DEMO_TRACES
        self._faults = faults or {}

    def get_trace(self, order_no: str) -> TraceResult:
        faults = self._faults

        delay = float(faults.get("logistics.delay_seconds", 0) or 0)
        if delay > 0:
            time.sleep(min(delay, 10.0))

        if faults.get("logistics.timeout"):
            logger.warning(f"[MockTraceProvider] 注入故障: timeout (order={order_no})")
            raise TraceProviderError(f"物流轨迹服务超时 (order={order_no})")

        if faults.get("logistics.error"):
            logger.warning(f"[MockTraceProvider] 注入故障: error (order={order_no})")
            raise TraceProviderError(f"物流轨迹服务错误 (order={order_no})")

        events = list(self._traces.get(order_no, []))
        if faults.get("logistics.empty"):
            events = []

        return TraceResult(order_no=order_no, provider=self.provider_name, events=events)


_provider_instance: LogisticsTraceProvider | None = None
_provider_lock = threading.Lock()


def get_trace_provider() -> LogisticsTraceProvider | None:
    """轨迹 Provider 工厂。

    - demo 模式 + CS_DEMO_TRACE_PROVIDER=mock → MockTraceProvider（含故障注入）
    - 其余情况返回 None：调用方回退订单状态推导（真实 API 就绪后在此处
      按配置返回真实 Provider 实现）。
    """
    from backend.config import customer_service as cs_config

    if not is_demo_mode():
        return None
    if cs_config.CS_DEMO_TRACE_PROVIDER != "mock":
        return None

    global _provider_instance
    if _provider_instance is None:
        with _provider_lock:
            if _provider_instance is None:
                _provider_instance = MockTraceProvider(faults=dict(cs_config.CS_DEMO_FAULTS))
    return _provider_instance


def reset_trace_provider() -> None:
    """重置 Provider 单例（配置/故障开关变更与测试用）。"""
    global _provider_instance
    with _provider_lock:
        _provider_instance = None
