"""Prometheus 文本格式指标导出端点。"""
from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest


def render_metrics() -> tuple[bytes, str]:
    """生成 Prometheus 文本格式输出。

    Returns:
        (body, content_type) — 给 FastResponse 直接用
    """
    return generate_latest(), CONTENT_TYPE_LATEST
