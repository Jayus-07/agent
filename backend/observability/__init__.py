"""observability — 统一可观测层（PR-2.x 从 rag/ + orchestration/ + middleware/ + shared/ 收敛）。

模块:
  - tracer.py:        TraceCollector — 结构化 LLM/RAG trace 收集
  - trace_store.py:   SQLite 持久化存储
  - metrics.py:       Prometheus /metrics 端点
  - alerts.py:        告警构造
  - topology.py:      Graph 拓扑定义 (LangGraph + Indexing)
  - resource.py:      系统资源监控
"""
# 核心 tracer（其他模块直接依赖）
from backend.observability.tracer import (  # noqa: F401
    TraceCollector, TraceRecord, Span, SpanKind, WorkflowKind,
    trace_collector,
)
# 拓扑（前端渲染用）
from backend.observability.topology import (  # noqa: F401
    GRAPH_TOPOLOGY, NODE_LABELS,
    INDEXING_TOPOLOGY, INDEXING_LABELS,
    TOPOLOGY_REGISTRY, get_topology,
)
