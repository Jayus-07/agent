"""Prometheus 指标 + /metrics 端点 — PR-0.3。

4 个核心 SLI（对齐 docs/observability/slo.md 的 SLO 目标）：
- chat_request_total{status}        Counter
- chat_request_duration_seconds     Histogram
- llm_tokens_total{model, direction} Counter
- skill_failure_total{skill, error_type} Counter

业务模块调用方式：
    from backend.observability.metrics import (
        chat_request_total, chat_request_duration_seconds,
        llm_tokens_total, skill_failure_total,
    )
    chat_request_total.labels(status="ok").inc()

HTTP 端点 /metrics 在 server.py 注册。

4 个运营指标（2026-08-11 PRD+TRD 完善计划）：
- rag_hit_rate           Gauge — RAG 命中率
- rag_reject_rate        Gauge — RAG 拒答率（Evidence Gate 拦截）
- doc_metadata_coverage  Gauge — 活跃文档 metadata 完整度
- feedback_positive_rate Gauge — 用户反馈 👍 比例

物理拆解架构说明：
    底层拆解至 backend.observability.metrics_domain 子包：
    - base: 基础目录与多进程环境初始化
    - stream_tracker: 流式耗时追踪器 (StreamLatencyTracker) 与 TPOT/TTFT 指标
    - core_sli: 核心 4 SLI、Token 计费预算、并发控制与 SSE 执行池指标
    - rag_metrics: RAG 阶段耗时、检索命中、短路拒答与分块指标
    - router_metrics: 意图分类、分层裁决与 FC 工具选择指标
    - cs_metrics: 客服意图、工单完整性、Outbox 队列与派单指标
    - infra_metrics: 任务准入调度、Worker 运行时、Trace 质量与熔断器指标
    - exporter: Prometheus 输出端点 (render_metrics)
    本模块作为 100% 向后兼容门面，对外统一重新导出所有指标与函数。
"""
from __future__ import annotations

from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest  # noqa: F401

import backend.observability.metrics_domain as _metrics_domain
from backend.observability.metrics_domain import *  # noqa: F403
from backend.observability.metrics_domain.base import _mp_dir  # noqa: F401
from backend.observability.metrics_domain.infra_metrics import _feedback_state  # noqa: F401
from backend.observability.metrics_domain.rag_metrics import _rag_state  # noqa: F401
from backend.observability.metrics_domain.router_metrics import _LAYER_ALIASES  # noqa: F401

__all__ = _metrics_domain.__all__


def __getattr__(name: str) -> Any:
    """动态透传未显式导出的内部或扩展属性，保障测试 monkeypatch 与向后兼容。"""
    try:
        return getattr(_metrics_domain, name)
    except AttributeError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
