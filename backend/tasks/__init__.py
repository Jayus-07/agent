"""tasks — Celery 异步任务编排层。

边界约束（架构红线）：
- Celery 只负责任务调度与投递，不承载 Agent 执行状态；
- Agent 状态恢复完全依赖 LangGraph PostgresSaver checkpoint；
- Skill 层 / 域图层不感知本模块（不 import backend.tasks）。
"""
from backend.tasks.celery_app import celery_app

__all__ = ["celery_app"]
