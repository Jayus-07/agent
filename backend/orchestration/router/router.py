"""router.py — Router 主流程（3 层 fallback，2026-08-11）

  Rule Router（1ms）
    ↓ confidence < 0.8
  Embedding Router（~30ms）
    ↓ confidence < 0.85
  LLM Router（~3-5s，qwen 本地）
    ↓
  RouteDecision
"""
from __future__ import annotations

import asyncio
import time

from backend.orchestration.router.types import (
    CapabilityScore,
    ExecutionMode,
    RouteDecision,
)
from backend.orchestration.router.rule_router import RuleRouter
from backend.orchestration.router.vector_router import VectorRouter
from backend.orchestration.router.llm_router import LLMRouter
from backend.observability import trace_collector
from backend.observability.tracer import SpanKind
from backend.shared.logger import logger


class Router:
    """3 层 fallback Router。"""

    def __init__(self, llm_timeout: int = 20):
        self.rule = RuleRouter()
        self.vector = VectorRouter()
        self.llm = LLMRouter(timeout=llm_timeout)

    def route(self, query: str) -> RouteDecision:
        """同步路由入口 — 含 Trace Span（每层判断结果记录为 event）。

        链路:
          1. Rule (0.001s)
          2. Embedding (0.03s)
          3. LLM (3-5s)
        """
        from backend.observability.metrics import record_router_decision

        t0 = time.time()

        # ── Trace Span: 路由决策 ──
        span = trace_collector.start_span(
            "router", name="路由决策", kind=SpanKind.ROUTER.value,
            input={"query": query},
        )
        final_layer = "llm"  # 默认 LLM 兜底

        # 1. Rule Router（强信号）
        result = self.rule.route(query)
        rule_conf = result.confidence if result else 0.0
        rule_matched = result is not None and result.confidence >= 0.8
        trace_collector.add_event(
            span, "rule_check", "info",
            f"Rule 层: matched={rule_matched} confidence={rule_conf:.2f}",
            {"layer": "rule", "matched": rule_matched, "confidence": rule_conf},
        )
        if rule_matched:
            final_layer = "rule"
            logger.info(
                f"[Router] Rule 命中: {result.reason} (latency={int((time.time()-t0)*1000)}ms)"
            )
            record_router_decision(result.execution_mode.value, "rule", result.confidence)
            trace_collector.end_span(
                span,
                output=result.model_dump(),
                metrics={"layer": final_layer, "confidence": result.confidence,
                         "mode": result.execution_mode.value},
                status="success",
            )
            return result

        # 2. Embedding Router（语义匹配）
        result = self.vector.route(query)
        vec_conf = result.confidence if result else 0.0
        vec_matched = result is not None and result.confidence >= 0.85
        vec_top = result.candidates[0].name if (result and result.candidates) else ""
        trace_collector.add_event(
            span, "vector_check", "info",
            f"Embedding 层: matched={vec_matched} confidence={vec_conf:.2f} top={vec_top}",
            {"layer": "vector", "matched": vec_matched, "confidence": vec_conf,
             "top_capability": vec_top},
        )
        if vec_matched:
            final_layer = "embedding"
            logger.info(
                f"[Router] Embedding 命中: {result.reason} (latency={int((time.time()-t0)*1000)}ms)"
            )
            record_router_decision(result.execution_mode.value, "embedding", result.confidence)
            trace_collector.end_span(
                span,
                output=result.model_dump(),
                metrics={"layer": final_layer, "confidence": result.confidence,
                         "mode": result.execution_mode.value},
                status="success",
            )
            return result

        # 3. LLM Router（兜底）
        result = self.llm.route(query)
        llm_conf = result.confidence if result else 0.0
        trace_collector.add_event(
            span, "llm_check", "info",
            f"LLM 兜底: confidence={llm_conf:.2f} reason={result.reason}",
            {"layer": "llm", "matched": True, "confidence": llm_conf,
             "reason": result.reason or ""},
        )
        logger.info(
            f"[Router] LLM 兜底: {result.reason} (latency={int((time.time()-t0)*1000)}ms)"
        )
        record_router_decision(result.execution_mode.value, "llm", result.confidence)
        trace_collector.end_span(
            span,
            output=result.model_dump(),
            metrics={"layer": final_layer, "confidence": result.confidence,
                     "mode": result.execution_mode.value},
            status="success",
        )
        return result

    async def aroute(self, query: str) -> RouteDecision:
        """异步路由入口（FastAPI 场景）。"""
        return await asyncio.to_thread(self.route, query)


# ── 模块级单例 ──
_router_instance: Router | None = None


def get_router() -> Router:
    """获取 Router 单例（首次调用时初始化）。"""
    global _router_instance
    if _router_instance is None:
        _router_instance = Router()
    return _router_instance
