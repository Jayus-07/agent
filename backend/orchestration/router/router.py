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

        # 1. Rule Router（1ms，关键词匹配）
        result = self.rule.route(query)
        if result is None:
            # 无任何匹配 → 直接跳到下一层
            trace_collector.add_event(
                span, "rule_miss", "info",
                "Rule 层: 无匹配",
                {"layer": "rule", "verdict": "miss", "confidence": 0},
            )
        elif result.confidence >= 0.8:
            # 强信号 → Rule 拍板
            final_layer = "rule"
            trace_collector.add_event(
                span, "rule_decide", "info",
                f"Rule 决定: {result.reason} (confidence={result.confidence:.2f})",
                {"layer": "rule", "verdict": "decide", "confidence": result.confidence,
                 "candidate": result.candidates[0].name if result.candidates else "",
                 "reason": result.reason or ""},
            )
            logger.info(
                f"[Router] Rule 决定: {result.reason} (latency={int((time.time()-t0)*1000)}ms)"
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
        else:
            # 弱信号 → 给 hint，交给下层
            trace_collector.add_event(
                span, "rule_hint", "info",
                f"Rule 提示: {result.reason} (confidence={result.confidence:.2f} < 0.8)",
                {"layer": "rule", "verdict": "hint", "confidence": result.confidence,
                 "candidate": result.candidates[0].name if result.candidates else "",
                 "reason": result.reason or ""},
            )

        # 2. Embedding Router（~30ms，语义匹配）
        result = self.vector.route(query)
        if result is not None and result.confidence >= 0.85:
            final_layer = "embedding"
            trace_collector.add_event(
                span, "vector_decide", "info",
                f"Vector 决定: {result.reason} (confidence={result.confidence:.2f})",
                {"layer": "vector", "verdict": "decide", "confidence": result.confidence,
                 "candidate": result.candidates[0].name if result.candidates else ""},
            )
            logger.info(
                f"[Router] Vector 决定: {result.reason} (latency={int((time.time()-t0)*1000)}ms)"
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
        else:
            vec_conf = result.confidence if result else 0.0
            vec_top = result.candidates[0].name if (result and result.candidates) else ""
            if result is None:
                trace_collector.add_event(
                    span, "vector_miss", "info",
                    "Vector 层: 无匹配",
                    {"layer": "vector", "verdict": "miss", "confidence": vec_conf},
                )
            else:
                trace_collector.add_event(
                    span, "vector_hint", "info",
                    f"Vector 提示: top={vec_top} (confidence={vec_conf:.2f} < 0.85)",
                    {"layer": "vector", "verdict": "hint", "confidence": vec_conf,
                     "candidate": vec_top},
                )

        # 3. LLM Router（~3-5s，真正理解 → 兜底拍板）
        result = self.llm.route(query)
        final_layer = "llm"
        llm_conf = result.confidence if result else 0.0
        trace_collector.add_event(
            span, "llm_decide", "info",
            f"LLM 决定: {result.reason} (confidence={llm_conf:.2f})",
            {"layer": "llm", "verdict": "decide", "confidence": llm_conf,
             "candidate": result.candidates[0].name if result.candidates else "",
             "reason": result.reason or ""},
        )
        logger.info(
            f"[Router] LLM 决定: {result.reason} (latency={int((time.time()-t0)*1000)}ms)"
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
