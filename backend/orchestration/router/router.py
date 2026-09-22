"""router.py — Router 主流程（2026-08-11 三层 fallback / 2026-09-22 分层路由接入）

  legacy（ROUTING_ARCHITECTURE=legacy，现行为）:
  Rule Router（1ms）
    ↓ confidence < 0.8
  Embedding Router（~30ms）
    ↓ confidence < 0.85
  LLM Router（~3-5s，qwen 本地）
    ↓
  RouteDecision

  hierarchical（ROUTING_ARCHITECTURE=hierarchical）:
  CoarseIntentClassifier（规则 hint → embedding 域心 → Confidence Gate）
    ↓
  Domain Tool Registry → FineToolRouter（Fast Path / 域内灰区交 tool_selector）
    ↓
  RouteDecision（routing_meta 携带粗分类/细选择明细）
  ※ QueryRouter 的 vector/LLM 层不再承担具体 Tool 选择（§14 重复路由收敛）

  shadow（ROUTING_SHADOW_MODE=true，叠加在 legacy 上）:
  legacy 拍板执行；hierarchical 同时计算 domain + fine top1（零 LLM），
  记录 legacy_tool / hierarchical_tool / is_match 供真实流量评估。
"""
from __future__ import annotations

import asyncio
import time

from backend.config import ROUTING_ARCHITECTURE, ROUTING_SHADOW_MODE
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

# 路由缓存键的上下文版本号。当前 RouteDecision 只依赖 query（mode/capability
# 与用户无关），context 仅作为**预留键位**参与键构造。一旦路由决策开始依赖
# 上下文（个性化能力、部门级路由等），递增此版本号即可让全部旧缓存自然失
# 效，无需清理。
_ROUTE_CACHE_CONTEXT_VERSION = "v1"


def _route_cache_key(query: str, context: dict | None = None) -> str:
    """构造路由缓存键：版本 + 排序后的上下文位 + query 原文。

    2026-09-17 前键只含 ``query.strip().lower()``——路由一旦未来依赖
    user/department 上下文，不同用户/部门的同文 query 会互串答案。
    现预留 context 键位：当前调用方传 department/user_id（同值不影响
    命中率），未来加维度只改调用方与版本号。
    """
    parts = [_ROUTE_CACHE_CONTEXT_VERSION]
    ctx = context or {}
    parts.extend(f"{k}={ctx[k]}" for k in sorted(ctx))
    parts.append(query.strip().lower())
    return "|".join(parts)


class Router:
    """3 层 fallback Router。"""

    def __init__(self, llm_timeout: int | None = None):
        self.rule = RuleRouter()
        self.vector = VectorRouter()
        # 兜底层超时由 ROUTER_LLM_TIMEOUT 统一控制（默认 6s，原 12s）
        self.llm = LLMRouter(timeout=llm_timeout)

    def route(self, query: str, context: dict | None = None) -> RouteDecision:
        """同步路由入口 — 含 Trace Span（每层判断结果记录为 event）。

        context: 路由缓存键的上下文位（预留）。当前决策不依赖它，但键里
        会带上——未来路由个性化时无需迁移缓存语义。router_node 传
        {department, user_id}。
        """
        if ROUTING_ARCHITECTURE == "hierarchical":
            t0 = time.time()
            span = trace_collector.start_span(
                "router", name="路由决策", kind=SpanKind.ROUTER.value,
                input={"query": query, "architecture": "hierarchical"},
            )
            cached_h = self._get_hierarchical_cached(query, context)
            if cached_h is not None:
                trace_collector.add_event(
                    span, "cache_hit", "info", "分层路由缓存命中",
                    {"layer": "cache", "architecture": "hierarchical"},
                )
                trace_collector.end_span(span, output=cached_h.model_dump(),
                                         metrics={"layer": "cache", "architecture": "hierarchical"},
                                         status="success")
                return cached_h
            try:
                from backend.orchestration.router.hierarchical import get_hierarchical_router

                decision = get_hierarchical_router().route(query, context)
            except Exception as e:
                # 粗分类 degraded（embedding 不可用等）→ 回退 legacy 三层路由
                logger.warning(f"[Router] hierarchical 路由失败，回退 legacy: {e}")
                trace_collector.add_event(
                    span, "hierarchical_fallback", "warning",
                    f"分层路由回退 legacy: {e}",
                    {"layer": "hierarchical", "verdict": "fallback"},
                )
            else:
                trace_collector.add_event(
                    span, "hierarchical_decide", "info",
                    decision.reason or "hierarchical",
                    {"layer": "hierarchical", "verdict": "decide",
                     "domain": (decision.routing_meta or {}).get("domain", ""),
                     "confidence": decision.confidence},
                )
                self._record_hierarchy_metrics(decision, span)
                trace_collector.end_span(
                    span, output=decision.model_dump(),
                    metrics={"layer": "hierarchical", "confidence": decision.confidence,
                             "mode": decision.execution_mode.value},
                    status="success",
                )
                self._set_hierarchical_cached(query, context, decision)
                return decision
            # 回退 legacy：结束本 span，交 legacy 三层路由收口（shadow 挂钩同样生效）
            trace_collector.end_span(
                span, output={"fallback": "legacy"},
                metrics={"layer": "hierarchical", "verdict": "fallback"},
                status="success",
            )
            return self._route_with_shadow(query, context)

        # legacy 模式（shadow mode 挂钩在此）
        if ROUTING_SHADOW_MODE:
            return self._route_with_shadow(query, context)
        return self._route_legacy(query, context)

    def _route_with_shadow(self, query: str, context: dict | None) -> RouteDecision:
        """legacy 决策 + shadow 对比（§20 双轨评估，legacy 结果不变）。"""
        result = self._route_legacy(query, context)
        if ROUTING_SHADOW_MODE:
            self._run_shadow(query, result)
        return result

    def _run_shadow(self, query: str, legacy_result: RouteDecision) -> None:
        """shadow：hierarchical 同步计算（零 LLM），记录 legacy vs 新链路对比。"""
        try:
            from backend.observability.metrics import record_shadow_match
            from backend.orchestration.router.hierarchical import get_hierarchical_router

            comparison = get_hierarchical_router().shadow_compare(query, legacy_result)
            if comparison is None:
                return
            record_shadow_match(comparison["is_match"])
            trace = trace_collector.current()
            if trace is not None:
                trace.metadata["routing_shadow"] = comparison
            logger.info(
                f"[Router] shadow: legacy={comparison['legacy_tool'] or comparison['legacy_mode']} "
                f"hierarchical={comparison['hierarchical_domain']}/"
                f"{comparison['hierarchical_tool'] or '-'} "
                f"match={comparison['is_match']}"
            )
        except Exception as e:
            logger.debug(f"[Router] shadow 对比失败（不影响主流程）: {e}")

    def _route_legacy(self, query: str, context: dict | None = None) -> RouteDecision:
        """legacy 三层路由主体（rule → vector → LLM）。"""
        from backend.observability.metrics import record_router_decision

        t0 = time.time()

        # ── Trace Span: 路由决策 ──
        span = trace_collector.start_span(
            "router", name="路由决策", kind=SpanKind.ROUTER.value,
            input={"query": query},
        )
        final_layer = "llm"  # 默认 LLM 兜底

        # ── 缓存命中：跳过全部 3 层路由 ──
        _cached_data = _router_cache.get_json(_route_cache_key(query, context))
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
            _router_cache.set_json(_route_cache_key(query, context), result.model_dump())
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
            _router_cache.set_json(_route_cache_key(query, context), result.model_dump())
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
            _router_cache.set_json(_route_cache_key(query, context), result.model_dump())
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
                    # 观测口径（路由专项 Step 1）：trace 保留 upstream 原始值
                    "upstream_model_id": _m.get("upstream_model_id", ""),
                    "configured_model_id": _m.get("configured_model_id", ""),
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
        _router_cache.set_json(_route_cache_key(query, context), result.model_dump())
        return result

    def route_legacy(self, query: str, context: dict | None = None) -> RouteDecision:
        """显式走 legacy 三层路由（绕过 hierarchical 分支）。

        分层路由模式下，粗分类命中的域图 prefilter 未放行（如 CS 灰度
        control 组）时由 router_node 调用本方法回退，避免因路由缓存命中
        又拿到同一个 hierarchical 决策造成死循环。
        """
        return self._route_with_shadow(query, context)

    async def aroute(self, query: str, context: dict | None = None) -> RouteDecision:
        """异步路由入口（FastAPI 场景）。"""
        return await asyncio.to_thread(self.route, query, context)

    # ── hierarchical 决策缓存与指标（2026-09-22 分层路由）─────────

    def _get_hierarchical_cached(self, query: str, context: dict | None) -> RouteDecision | None:
        """hierarchical 决策缓存：与 legacy 共用存储、键带架构位隔离，
        避免切换 ROUTING_ARCHITECTURE 后读到对方语义的旧决策。"""
        data = _router_cache.get_json(
            _route_cache_key(query, {**(context or {}), "arch": "hierarchical"}))
        if data is None:
            return None
        try:
            return RouteDecision(**data)
        except Exception:
            return None

    def _set_hierarchical_cached(self, query: str, context: dict | None,
                                 decision: RouteDecision) -> None:
        _router_cache.set_json(
            _route_cache_key(query, {**(context or {}), "arch": "hierarchical"}),
            decision.model_dump(),
        )

    def _record_hierarchy_metrics(self, decision: RouteDecision, span) -> None:
        """分层路由指标 + span 事件（软失败不影响主流程）。"""
        try:
            from backend.observability.metrics import (
                record_domain_classification,
                record_hierarchy_verdict,
                record_routing_latency,
            )

            meta = decision.routing_meta or {}
            domain = meta.get("domain") or "unknown"
            record_domain_classification(domain, meta.get("domain_source") or meta.get("architecture", ""))
            action = meta.get("domain_action") or "legacy_fallback"
            if action == "tool_route":
                verdict = "fast_path" if meta.get("tool_route_mode") == "fast_path" else "llm_selector"
            elif action == "clarify":
                verdict = "clarification"
            else:
                verdict = action
            record_hierarchy_verdict(verdict)
            record_routing_latency("hierarchical_total", meta.get("routing_latency_ms") or 0)
            trace_collector.add_event(
                span, "coarse_classifier", "info",
                f"粗分类: domain={domain} ({meta.get('domain_source')}, "
                f"conf={meta.get('domain_confidence')}, margin={meta.get('domain_margin')})",
                {"stage": "coarse_classifier", "domain": domain,
                 "confidence": meta.get("domain_confidence", 0),
                 "margin": meta.get("domain_margin", 0),
                 "source": meta.get("domain_source", ""), "reason_code": meta.get("reason_code", "")},
            )
            if meta.get("candidate_tool_count"):
                trace_collector.add_event(
                    span, "domain_tool_resolution", "info",
                    f"域内候选 {meta['candidate_tool_count']} 个: "
                    f"{','.join(meta.get('candidate_tools', []))}",
                    {"stage": "domain_tool_resolution",
                     "candidate_tool_count": meta["candidate_tool_count"]},
                )
                trace_collector.add_event(
                    span, "fine_tool_router", "info",
                    f"细路由: top1={meta.get('fine_top1')} "
                    f"(score={meta.get('fine_top1_score')}, margin={meta.get('fine_margin')}) "
                    f"→ {meta.get('tool_route_mode')}",
                    {"stage": "fine_tool_router", "top1": meta.get("fine_top1", ""),
                     "top1_score": meta.get("fine_top1_score", 0),
                     "margin": meta.get("fine_margin", 0),
                     "route_mode": meta.get("tool_route_mode", "")},
                )
        except Exception:
            pass


# ── 模块级单例 ──
_router_instance: Router | None = None


def get_router() -> Router:
    """获取 Router 单例（首次调用时初始化）。"""
    global _router_instance
    if _router_instance is None:
        _router_instance = Router()
    return _router_instance
