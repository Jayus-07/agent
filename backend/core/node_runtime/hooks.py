"""node_runtime/hooks.py — 节点观测钩子

runner 在生命周期点位回调钩子；钩子实现必须自行软失败（参照
record_cs_expert_result / span 埋点的既有内部兜底），runner 不兜底钩子异常
——钩子有 bug 应当暴露而不是被吞。

域差异（STOP F 冻结，禁止互相补齐）：
- CsExpertHooks：metrics-only（record_cs_expert_result）+ 日志，无 span——
  给 CS 专家补 span 属 trace 形态变更，已登记 Deferred 须独立评审；
- TravelExpertHooks：travel_expert_{name} span 生命周期（软失败）+ span
  metrics 搭载 + 日志，无领域 metrics。

注意：CsExpertHooks 对 metrics 的导入必须保持在调用时（方法体内 import），
回归测试以 @patch("backend.observability.metrics.record_cs_expert_result")
注入打点；改成模块级导入会让 patch 失效。
"""
from __future__ import annotations

from backend.core.node_runtime.context import ExecutionContext
from backend.shared.logger import logger


class ObservabilityHooks:
    """默认 noop 钩子。"""

    def on_start(self, ctx: ExecutionContext) -> None:
        return None

    def on_success(self, ctx: ExecutionContext, *, status: str,
                   duration_ms: int) -> None:
        return None

    def on_error(self, ctx: ExecutionContext, *, kind: str, duration_ms: int,
                 exc: BaseException | None = None) -> None:
        # kind: "timeout"（限时窗口超时，exc=None）| "exception"（fn 抛异常）
        return None


class CsExpertHooks(ObservabilityHooks):
    """CS 专家观测：start/done 日志 + record_cs_expert_result（metrics-only）。"""

    def on_start(self, ctx: ExecutionContext) -> None:
        logger.info("[CS Expert] start expert=%s", ctx.node_name)

    def on_success(self, ctx: ExecutionContext, *, status: str,
                   duration_ms: int) -> None:
        from backend.observability.metrics import record_cs_expert_result

        record_cs_expert_result(ctx.node_name, status)
        logger.info(
            "[CS Expert] done expert=%s status=%s duration_ms=%d",
            ctx.node_name, status, duration_ms,
        )

    def on_error(self, ctx: ExecutionContext, *, kind: str, duration_ms: int,
                 exc: BaseException | None = None) -> None:
        from backend.observability.metrics import record_cs_expert_result

        if kind == "timeout":
            logger.error(
                "[CS Expert] timeout expert=%s after %.1fs",
                ctx.node_name, ctx.deadline,
            )
            record_cs_expert_result(ctx.node_name, "timeout")
        else:
            logger.error(
                "[CS Expert] exception expert=%s: %s", ctx.node_name, exc,
                exc_info=exc,
            )
            record_cs_expert_result(ctx.node_name, "failed")


class TravelExpertHooks(ObservabilityHooks):
    """旅游专家观测：start/done 日志 + travel_expert_{name} span（软失败）。

    span 开启/收口的软失败语义从 travel/experts/base.py 原样迁移：
    无活跃 trace 时为 noop span，任何埋点异常都不影响专家执行。
    旅游专家是纯规则快路径：无 timeout（kind 恒为 exception）、无领域 metrics。
    """

    def __init__(self) -> None:
        self._span = None

    def on_start(self, ctx: ExecutionContext) -> None:
        logger.info("[Travel Expert] start expert=%s", ctx.node_name)
        try:
            from backend.observability.tracer import trace_collector
            self._span = trace_collector.start_span(
                f"travel_expert_{ctx.node_name}", name=f"旅游专家:{ctx.node_name}",
                type="agent", kind="agent", input={},
            )
        except Exception:
            logger.debug("[Travel Expert] span 开启失败（不影响执行）", exc_info=True)
            self._span = None

    def on_success(self, ctx: ExecutionContext, *, status: str,
                   duration_ms: int) -> None:
        self._end_span(status, duration_ms)
        logger.info("[Travel Expert] done expert=%s status=%s duration_ms=%d",
                    ctx.node_name, status, duration_ms)

    def on_error(self, ctx: ExecutionContext, *, kind: str, duration_ms: int,
                 exc: BaseException | None = None) -> None:
        self._end_span("failed", duration_ms,
                       error=str(exc) if exc is not None else "")
        logger.exception("[Travel Expert] exception expert=%s", ctx.node_name)

    def _end_span(self, status: str, duration_ms: int, error: str = "") -> None:
        if self._span is None:
            return
        try:
            from backend.observability.tracer import trace_collector
            metrics = {"expert_status": status, "duration_ms": duration_ms}
            if error:
                metrics["error"] = error
            trace_collector.end_span(
                self._span, output={"status": status}, metrics=metrics,
                status="error" if status == "failed" else "success",
            )
        except Exception:
            logger.debug("[Travel Expert] span 收口失败", exc_info=True)
