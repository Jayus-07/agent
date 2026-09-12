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

from backend.infra.cache import get_cache
from backend.observability import trace_collector
from backend.observability.tracer import SpanKind
from backend.orchestration.router.llm_router import LLMRouter
from backend.orchestration.router.rule_router import RuleRouter
from backend.orchestration.router.types import (
    RouteDecision,
)
from backend.orchestration.router.vector_router import VectorRouter
from backend.shared.logger import logger

_router_cache = get_cache("router", ttl=300)


class Router:
    """3 层 fallback Router。"""

    def __init__(self, llm_timeout: int = 12):
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

        # ── 缓存命中：跳过全部 3 层路由 ──
        _cached_data = _router_cache.get_json(query.strip().lower())
        cached = RouteDecision(**_cached_data) if _cached_data is not None else None
        if cached is not None:
            trace_collector.add_event(
                span, "cache_hit", "info",
                f"路由缓存命中: {cached.candidates[0].name if cached.candidates else '?'}",
                {"layer": "cache", "mode": cached.execution_mode.value},
            )
            trace_collector.end_span(
                span,
                output=cached.model_dump(),
                metrics={"layer": "cache", "confidence": cached.confidence,
                         "mode": cached.execution_mode.value},
                status="success",
            )
            logger.info(f"[Router] 缓存命中: query={query[:40]}... (latency={int((time.time()-t0)*1000)}ms)")
            return cached

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
            _router_cache.set_json(query.strip().lower(), result.model_dump())
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
        vec_conf = result.confidence if result else 0.0
        vec_has_candidates = bool(result and result.candidates)
        if result is not None and vec_conf >= 0.85:
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
            _router_cache.set_json(query.strip().lower(), result.model_dump())
            return result
        elif vec_has_candidates and vec_conf >= 0.6:
            final_layer = "embedding"
            trace_collector.add_event(
                span, "vector_accept_moderate", "info",
                f"Vector 中置信度采纳: {result.reason} (confidence={vec_conf:.2f}, 跳过LLM)",
                {"layer": "embedding", "verdict": "accept_moderate", "confidence": vec_conf,
                 "candidate": result.candidates[0].name if result.candidates else ""},
            )
            logger.info(
                f"[Router] Vector 中置信度采纳: top={result.candidates[0].name} "
                f"(confidence={vec_conf:.2f}, latency={int((time.time()-t0)*1000)}ms)"
            )
            record_router_decision(result.execution_mode.value, "embedding", result.confidence)
            trace_collector.end_span(
                span,
                output=result.model_dump(),
                metrics={"layer": final_layer, "confidence": result.confidence,
                         "mode": result.execution_mode.value},
                status="success",
            )
            _router_cache.set_json(query.strip().lower(), result.model_dump())
            return result
        else:
            vec_top = result.candidates[0].name if (result and result.candidates) else ""
            if not vec_has_candidates:
                trace_collector.add_event(
                    span, "vector_miss", "info",
                    "Vector 层: 无匹配",
                    {"layer": "vector", "verdict": "miss", "confidence": vec_conf},
                )
            else:
                trace_collector.add_event(
                    span, "vector_hint", "info",
                    f"Vector 提示: top={vec_top} (confidence={vec_conf:.2f} < 0.6, 需LLM)",
                    {"layer": "vector", "verdict": "hint", "confidence": vec_conf,
                     "candidate": vec_top},
                )

        # 3. LLM Router（~3-5s，真正理解 → 兜底拍板）
        # P1-5: 补 llm_call span — 旧实现只记 tool_call 事件，LLM 明细面板
        # 看不到这次最该被审计的调用（token/耗时/prompt 全缺失）。
        llm_span = trace_collector.start_span(
            "router_llm", name="路由 LLM", type="llm_call", kind=SpanKind.LLM.value,
            parent_id=span.span_id, input={"query": query[:500]},
        )
        try:
            result = self.llm.route(query)
        except Exception:
            trace_collector.end_span(llm_span, status="error")
            raise
        # proxy 同步调用后 ContextVar 内有本次调用的 token/cost（读不到由
        # tracer finish 的 llm_usage 回填兜底）
        try:
            from backend.infra.llm.proxy import _last_call_meta_var
            _m = dict(_last_call_meta_var.get() or {})
            if _m.get("total_tokens"):
                llm_span.metrics.update({
                    "prompt_tokens": _m.get("prompt_tokens", 0),
                    "completion_tokens": _m.get("completion_tokens", 0),
                    "total_tokens": _m.get("total_tokens", 0),
                    "cost_usd": _m.get("cost_usd", 0),
                    "model_name": _m.get("model", ""),
                })
        except Exception:
            pass
        trace_collector.end_span(llm_span, metrics={"router_layer": "llm"})
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
        _router_cache.set_json(query.strip().lower(), result.model_dump())
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
