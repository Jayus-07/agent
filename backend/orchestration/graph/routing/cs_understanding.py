"""routing/cs_understanding.py — CS 理解增强（P1-2 拆自 router_node.py）

迁入内容（2026-09-30 纯移动，函数体逐字保留）：
  - _enrich_with_understanding：CSUnderstanding 结果并入 cs_route

追加（2026-10-08 客服域 LLM 语义层收口）：
  - _enrich_with_semantic：规则理解层之后的 LLM 语义增强（intent 补判 +
    语义槽位候选）。Rule First：规则明确则 LLM 0 次；关闭开关或任何失败
    软降级，行为与改造前一致。
"""
from __future__ import annotations

from backend.shared.logger import logger


def _enrich_with_semantic(cs_update: dict, query: str) -> dict:
    """LLM 语义理解增强（2026-10-08）：规则层之后、CS 图之前执行。

    产出（全部经 validator 白名单）：
      - rule miss/低置信时 LLM intent 补判 → cs_route.intent 改写（画像派生）
      - 意图需要业务对象且无显式订单号 → 语义槽位候选 metadata.semantic_slots
        （真实对象解析在专家层用真实 user_id 完成，router 层零新增 IO）
    软降级：开关关闭/LLM 失败/异常 → cs_route 保持规则层原样。
    """
    try:
        from backend.customer_service.understanding.semantic import (
            enrich_semantic_understanding,
            semantic_layer_enabled,
        )

        if not semantic_layer_enabled():
            return cs_update

        ctx = cs_update.get("cs_context")
        cs_route = ctx.get("cs_route") if isinstance(ctx, dict) else None
        if not isinstance(cs_route, dict):
            cs_route = cs_update.get("cs_route")
        if not isinstance(cs_route, dict):
            return cs_update

        enrich_semantic_understanding(cs_route, query)
        return cs_update
    except Exception as e:
        logger.warning(f"[RouterNode] CS 语义增强软降级: {e}")
        return cs_update


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
        # LLM 语义增强（2026-10-08）：规则层之后执行，软降级不阻塞路由
        _enrich_with_semantic(cs_update, query)
        return cs_update
    except Exception as e:
        logger.warning(f"[RouterNode] CSUnderstanding 软降级: {e}")
        return cs_update
