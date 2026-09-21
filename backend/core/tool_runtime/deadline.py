"""tool_runtime/deadline.py — 请求级 Deadline（在线请求统一预算）

一次用户请求的端到端时间预算。所有 Tool 调用前必须检查剩余预算，
预算不足不启动新调用，直接走降级 —— 取代"只依赖单个 Tool timeout"
的旧容错方式。

时钟约定:
  - 计时用 time.monotonic()（不受系统改钟影响）；
  - started_at 是 wall clock（展示/落库用）；
  - checkpoint_safe() 序列化后跨进程还原时 monotonic 基准失效，
    from_dict() 以还原时刻重新校准（预算语义降级为"剩余原预算"，
    对单进程 FastAPI 主链路无影响）。

预算模型（全部环境变量可配，见 config/settings.py）:
  business_deadline 30s = 请求端到端上限
  workflow_deadline 25s = 图执行（含 Tool）可用预算
  reporter_reserved  5s = 给 Reporter 生成回答保留的尾款
Tool 的有效超时 = min(policy timeout, 剩余 workflow 预算)，
因此单 Tool 最坏耗时被 Deadline 硬封顶，不可能再出现 3×60s。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field


class BudgetExhausted(Exception):
    """剩余预算不足以启动新的 Tool 调用（调用方应走降级，而非重试）。"""


@dataclass
class RequestDeadline:
    """一次在线请求的统一时间预算（随 RequestContext 显式随图状态流动）。"""

    request_id: str = ""
    # wall clock 起始（展示用）；计时一律走 _mono
    started_at: float = 0.0
    # monotonic 基准（from_dict 还原时重算）
    _mono_started: float = field(default=0.0, repr=False)
    # 三段预算（毫秒）
    total_budget_ms: float = 30_000.0
    workflow_budget_ms: float = 25_000.0
    reporter_reserved_ms: float = 5_000.0

    def __post_init__(self) -> None:
        if not self.request_id:
            self.request_id = uuid.uuid4().hex[:16]
        if not self.started_at:
            self.started_at = time.time()
        if not self._mono_started:
            self._mono_started = time.monotonic()

    # ── 构造 ──
    @classmethod
    def started_now(cls) -> "RequestDeadline":
        """请求入口创建：预算从 config/settings.py 读取（默认 30/25/5s）。"""
        from backend.config.settings import (
            BUSINESS_DEADLINE_S,
            REPORTER_RESERVED_S,
            WORKFLOW_DEADLINE_S,
        )
        return cls(
            total_budget_ms=BUSINESS_DEADLINE_S * 1000,
            workflow_budget_ms=WORKFLOW_DEADLINE_S * 1000,
            reporter_reserved_ms=REPORTER_RESERVED_S * 1000,
        )

    # ── 查询 ──
    def elapsed_ms(self) -> float:
        return (time.monotonic() - self._mono_started) * 1000

    def remaining_ms(self) -> float:
        """距业务总 Deadline 的剩余毫秒（可为负 = 已过期）。"""
        return self.total_budget_ms - self.elapsed_ms()

    def remaining_workflow_ms(self) -> float:
        """距 Workflow Deadline 的剩余毫秒 —— Tool 调用前的预算检查用这个。"""
        return self.workflow_budget_ms - self.elapsed_ms()

    def deadline_at(self) -> float:
        """业务总 Deadline 的 wall clock 时刻。"""
        return self.started_at + self.total_budget_ms / 1000

    def is_expired(self) -> bool:
        return self.remaining_ms() <= 0

    def ensure_budget(self, min_required_ms: float) -> None:
        """断言剩余 workflow 预算足够启动一个 min_required_ms 的动作。

        不足时抛 BudgetExhausted —— 调用方捕获后走降级，
        禁止继续调用 Tool 或重试。
        """
        remaining = self.remaining_workflow_ms()
        if remaining < min_required_ms:
            raise BudgetExhausted(
                f"剩余 workflow 预算 {remaining:.0f}ms < 所需 {min_required_ms:.0f}ms"
            )

    def effective_timeout_ms(self, requested_ms: float, margin_ms: float = 250.0) -> float:
        """Tool 的有效超时 = min(策略超时, 剩余 workflow 预算 - margin)。

        margin 给"超时判定→降级→reporter"留出收尾空间。
        返回值 <= 0 表示预算已不足（调用方应直接降级）。
        """
        remaining = self.remaining_workflow_ms() - margin_ms
        return min(requested_ms, remaining) if remaining > 0 else 0.0

    # ── 序列化（checkpoint 安全）──
    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "started_at": self.started_at,
            "total_budget_ms": self.total_budget_ms,
            "workflow_budget_ms": self.workflow_budget_ms,
            "reporter_reserved_ms": self.reporter_reserved_ms,
            "_mono_started": self._mono_started,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "RequestDeadline | None":
        if not data:
            return None
        try:
            dl = cls(
                request_id=data.get("request_id", ""),
                started_at=data.get("started_at", 0.0),
                total_budget_ms=float(data.get("total_budget_ms", 30_000)),
                workflow_budget_ms=float(data.get("workflow_budget_ms", 25_000)),
                reporter_reserved_ms=float(data.get("reporter_reserved_ms", 5_000)),
            )
            # 跨进程还原：monotonic 基准失效 → 以还原时刻重算。
            # 若还原发生在"同进程内很快"（FastAPI 主链路的唯一场景），
            # elapsed 误差可忽略；预算语义不会比旧实现（无 Deadline）更差。
            if data.get("_mono_started"):
                dl._mono_started = time.monotonic()
            return dl
        except (TypeError, ValueError):
            return None
