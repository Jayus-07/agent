"""RAG Chain 阶段指标构造与防线降级辅助。"""
from __future__ import annotations

from backend.rag.chain_modules.compat import get_chain_context
from backend.shared.logger import logger


def _build_retrieval_span_metrics(context_docs: list) -> dict[str, int]:
    """构造统一的检索阶段计数，供 Trace 与验收直接消费。"""
    counts = {}
    try:
        counts = (get_chain_context().meta or {}).get("_retrieval_counts") or {}
    except Exception:  # noqa: BLE001 — 观测字段失败不阻断主链
        logger.debug("读取检索计数失败", exc_info=True)

    def _int_count(key: str) -> int:
        try:
            return max(int(counts.get(key, 0) or 0), 0)
        except (TypeError, ValueError):
            return 0

    return {
        "total_docs": len(context_docs),
        "vector_count": _int_count("vector_count"),
        "bm25_count": _int_count("bm25_count"),
        "fused_count": _int_count("fused_count"),
        "rerank_count": len(context_docs),
    }


def _mark_gate_degraded(layer: str, action: str, error, span=None) -> None:
    """记录一次防线异常软降级：span 事件（如有 open span）+ Prometheus 计数。

    只补可见性，不改变放行/拒答行为。span 为 None 时（无 open span 的
    降级点，如 prompt 回退、实体校验忽略）只记指标。
    """
    try:
        from backend.observability.metrics import rag_gate_degraded_total
        rag_gate_degraded_total.labels(layer=layer).inc()
    except Exception:  # noqa: BLE001
        pass
    if span is None:
        return
    try:
        from backend.observability.tracer import trace_collector
        trace_collector.add_event(
            span, "gate_degraded", level="warn",
            message=f"Gate 软降级: {layer}",
            data={"layer": layer, "action": action, "error": str(error)[:200]},
        )
    except Exception:  # noqa: BLE001
        pass
