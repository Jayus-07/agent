"""customer_service/router/cs_router.py — CS Router 门面

组合 DomainDetector + CSCoarseRouter + CSFineRouter:
  1. DomainDetector 已在 router_node 预过滤中完成（传入 detection）
  2. CoarseRouter → CSDomain
  3. FineRouter → intent
  4. 组装 CSRouteResult（含 kb_ids, route_path, requires_auth 等）
"""
from __future__ import annotations

import time

from backend.customer_service.router.coarse_router import CSCoarseRouter
from backend.customer_service.router.fine_router import CSFineRouter
from backend.customer_service.router.intents import (
    INTENT_PROFILES,
    kb_ids_for,
    resolve_route_path,
)
from backend.customer_service.router.types import (
    CSDetection,
    CSDomain,
    CSRouteResult,
)
from backend.infra.cache import get_cache
from backend.shared.logger import logger

_cs_cache = get_cache("cs_router", ttl=300)


class CSRouter:
    """客服路由门面 — 串联 coarse + fine 分类。"""

    def __init__(self):
        self.coarse = CSCoarseRouter()
        self.fine = CSFineRouter()

    def route(self, query: str, detection: CSDetection | None = None) -> CSRouteResult:
        """路由入口。

        Args:
            query: 用户问题
            detection: 来自 DomainDetector 的预检测结果（可选）
        """
        from backend.observability import trace_collector
        from backend.observability.tracer import SpanKind

        t0 = time.time()

        cache_key = f"cs:{query.strip().lower()}"
        cached_data = _cs_cache.get_json(cache_key)
        if cached_data is not None:
            logger.info(f"[CSRouter] 缓存命中 (latency={int((time.time()-t0)*1000)}ms)")
            return CSRouteResult(**cached_data)

        span = trace_collector.start_span(
            "cs_router", name="CS路由决策", kind=SpanKind.ROUTER.value,
            input={"query": query},
        )

        try:
            rule_hint = self._extract_hint(detection)

            domain, conf_coarse, reason_coarse = self.coarse.classify(query, rule_hint)
            intent, conf_fine, reason_fine = self.fine.classify(query, domain)

            confidence = round((conf_coarse + conf_fine) / 2.0, 3)

            profile = INTENT_PROFILES.get(intent)
            requires_auth = profile.requires_auth if profile else False
            requires_action = profile.requires_action if profile else False
            risk_level = profile.risk_level if profile else "low"
            route_path = resolve_route_path(intent)
            kb_ids = kb_ids_for(intent, domain)

            result = CSRouteResult(
                domain=domain,
                intent=intent,
                confidence=confidence,
                requires_auth=requires_auth,
                requires_action=requires_action,
                risk_level=risk_level,
                route_path=route_path,
                kb_ids=kb_ids,
                reason=f"coarse={reason_coarse}; fine={reason_fine}",
            )

            _cs_cache.set_json(cache_key, result.model_dump())

            trace_collector.end_span(
                span,
                output=result.model_dump(),
                metrics={
                    "domain": domain.value,
                    "intent": intent,
                    "confidence": confidence,
                    "route_path": route_path.value,
                },
                status="success",
            )

            logger.info(
                f"[CSRouter] {domain.value}/{intent} conf={confidence:.2f} "
                f"(latency={int((time.time()-t0)*1000)}ms)"
            )
            return result

        except Exception as e:
            logger.error(f"[CSRouter] 路由异常: {e}", exc_info=True)
            trace_collector.end_span(
                span,
                output={"error": str(e)},
                status="error",
            )
            return CSRouteResult(
                domain=CSDomain.UNKNOWN,
                intent="unknown",
                confidence=0.0,
                reason=f"error: {e}",
            )

    def _extract_hint(self, detection: CSDetection | None) -> CSDomain | None:
        """从 DomainDetector 结果提取粗分类 hint。"""
        if detection is None or not detection.rule_hits:
            return None
        from collections import Counter
        top_domain = Counter(detection.rule_hits).most_common(1)
        if top_domain:
            try:
                return CSDomain(top_domain[0][0])
            except ValueError:
                return None
        return None


_router_instance: CSRouter | None = None


def get_cs_router() -> CSRouter:
    global _router_instance
    if _router_instance is None:
        _router_instance = CSRouter()
    return _router_instance
