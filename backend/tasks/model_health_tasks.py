"""tasks/model_health_tasks.py — 模型健康周期探测（beat）。

薄壳：探测逻辑在 backend/infra/llm/health.py（与 Celery 解耦可测试）。
调度：celery_app.conf.beat_schedule `model.health_scan`，默认 300s
（MODEL_HEALTH_SCAN_INTERVAL 可调）。页面只读 llm_model_health 缓存。
"""
from __future__ import annotations

import contextvars
from collections.abc import Callable
from uuid import uuid4

from backend.shared.logger import logger
from backend.tasks.celery_app import celery_app


def _run_with_system_probe_budget(probe: Callable[[], dict]) -> dict:
    """让后台健康探测使用独立的系统预算与会话归属。"""
    request_id = f"model-health:{uuid4().hex}"

    def run():
        from backend.core.request_context import RequestContext
        from backend.infra.llm.budget import (
            bind_request_budget,
            clear_request_budget,
        )

        # 这是系统巡检，不归到某个用户，也不占用个人配额；仍使用全局
        # 请求预算限制，并把用量挂到唯一的巡检会话。Context 隔离防止
        # Celery 复用线程时把身份或预算泄漏到下一项任务。
        RequestContext(
            session_id=request_id,
            user_id="",
            tenant_id="",
        ).bind()
        bind_request_budget(request_id)
        try:
            return probe()
        finally:
            clear_request_budget()

    return contextvars.Context().run(run)


@celery_app.task(name="model.health_scan")
def model_health_scan() -> dict:
    """全量模型健康探测（低成本分档探测，结果写缓存表）。"""
    from backend.infra.llm.health import scan_all_models

    try:
        return _run_with_system_probe_budget(scan_all_models)
    except Exception:
        logger.error("[ModelHealthTask] 健康扫描失败", exc_info=True)
        return {"total": 0, "healthy": 0, "results": {}}


@celery_app.task(name="model.health_check_one")
def model_health_check_one(model_name: str) -> dict:
    """管理员手动触发单模型探测（API 同步路径也可直接调 probe_model）。"""
    from backend.infra.llm.health import probe_model

    return _run_with_system_probe_budget(lambda: probe_model(model_name))
