"""routing/cs_understanding.py — CS 理解增强（P1-2 拆自 router_node.py）

迁入内容（2026-09-30 纯移动，函数体逐字保留）：
  - _enrich_with_understanding：CSUnderstanding 结果并入 cs_route
"""
from __future__ import annotations

from backend.shared.logger import logger


def _enrich_with_understanding(cs_update: dict, query: str) -> dict:
    """P1 步骤 3 接线（2026-09-19）：CSUnderstanding 结果并入 cs_route。

    - 实体（订单号等）入 cs_route.metadata —— action/query expert 直接消费，
      取代各自散落的正则抽取入口；
    - normalized_text / missing_slots / decision_layer 随 cs_route 进状态；
    - decision_layer 写 Trace tags（P1 完成标准：路由 Trace 可查决策层）；
    - 纯规则零 LLM；任何异常软降级（不阻塞路由）。
    """
    try:
        from backend.customer_service.understanding import build_understanding
        # cs_prefilter 的产出：route_mode + cs_context（TypedDict，cs_route
        # 在 cs_context 内，兼容未来顶层直挂的形态）
        ctx = cs_update.get("cs_context")
        cs_route = ctx.get("cs_route") if isinstance(ctx, dict) else None
        if not isinstance(cs_route, dict):
            cs_route = cs_update.get("cs_route")
        if not isinstance(cs_route, dict):
            return cs_update
        u = build_understanding(query, cs_route)
        metadata = cs_route.setdefault("metadata", {})
        order_ids = u.entity_values("order_id")
        if order_ids:
            metadata.setdefault("order_id", order_ids[0])
        metadata["entities"] = [e.model_dump() for e in u.entities]
        metadata["missing_slots"] = u.missing_slots
        metadata["normalized_text"] = u.normalized_text
        metadata["decision_layer"] = u.decision_layer.value
        # 迁移 B5（2026-09-29）：情绪/风险信号随 metadata 进 CS 图状态——
        # 此前 signals 产出后无决策消费者（只进 Trace）。读方：
        # supervisor.make_supervisor_decision（风险兜底拦截 / P0 投诉直通）。
        metadata["sentiment"] = u.sentiment.value
        metadata["sentiment_hits"] = list(u.signals.get("sentiment") or [])
        metadata["urgency"] = u.urgency.value
        metadata["risk_hits"] = list(u.signals.get("risk") or [])
        try:
            from backend.observability.tracer import trace_collector
            t = trace_collector.current()
            if t is not None:
                t.tags["decision_layer"] = u.decision_layer.value
                if u.entities:
                    t.tags["cs_entities"] = ",".join(
                        f"{e.type.value}:{e.match()}" for e in u.entities[:6])
                if u.signals.get("risk"):
                    t.tags["cs_risk_hits"] = ",".join(u.signals["risk"][:6])
                if u.signals.get("sentiment"):
                    t.tags["cs_sentiment"] = u.sentiment.value
        except Exception:
            pass
        return cs_update
    except Exception as e:
        logger.warning(f"[RouterNode] CSUnderstanding 软降级: {e}")
        return cs_update
