"""tasks/model_health_tasks.py — 模型健康周期探测（beat）。

薄壳：探测逻辑在 backend/infra/llm/health.py（与 Celery 解耦可测试）。
调度：celery_app.conf.beat_schedule `model.health_scan`，默认 300s
（MODEL_HEALTH_SCAN_INTERVAL 可调）。页面只读 llm_model_health 缓存。
"""
from __future__ import annotations

from backend.shared.logger import logger
from backend.tasks.celery_app import celery_app


@celery_app.task(name="model.health_scan")
def model_health_scan() -> dict:
    """全量模型健康探测（低成本分档探测，结果写缓存表）。"""
    from backend.infra.llm.health import scan_all_models

    try:
        return scan_all_models()
    except Exception:
        logger.error("[ModelHealthTask] 健康扫描失败", exc_info=True)
        return {"total": 0, "healthy": 0, "results": {}}


@celery_app.task(name="model.health_check_one")
def model_health_check_one(model_name: str) -> dict:
    """管理员手动触发单模型探测（API 同步路径也可直接调 probe_model）。"""
    from backend.infra.llm.health import probe_model

    return probe_model(model_name)
