"""tasks/celery_app.py — Celery 应用工厂。

设计原则：
- Redis 同时作 broker 与 result backend（与业务缓存分库，默认 /1）
- 全部配置来自环境变量（backend.config.tasks），无硬编码
- 只调度不承载 Agent 状态：任务 payload 仅 task_id（json 可序列化）
- acks_late + prefetch=1：Worker 宕机任务自动回队；多实例公平消费
"""
from celery import Celery

from backend.config.tasks import (
    CELERY_BROKER_URL,
    CELERY_HARD_TASK_TIMEOUT,
    CELERY_RESULT_BACKEND,
    CELERY_TASK_TIMEOUT,
)

celery_app = Celery(
    "agent_tasks",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["backend.tasks.agent_tasks",     # Worker 启动自动注册任务模块
             "backend.tasks.index_tasks",     # 阶段4：RAG 上传索引队列化任务
             "backend.tasks.cs_maintenance_tasks",  # P2.4：客服全局维护（beat）
             "backend.tasks.signals"],        # 运行时埋点（worker/queue/耗时/异常）
)

celery_app.conf.update(
    # ── 序列化：payload 仅 task_id，json 足够（禁 pickle，安全 + 跨语言）──
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # ── 可靠性 ──
    task_acks_late=True,                 # 执行完才 ack：Worker 宕机任务回队重投
    worker_prefetch_multiplier=1,        # 长任务公平分发，防止单实例饿死队列
    task_reject_on_worker_lost=True,     # Worker 被 OOM kill 等异常退出 → 任务回队
    broker_connection_retry_on_startup=True,
    task_track_started=True,             # STARTED 状态可见（监控用）
    # 事件流（celery-exporter 消费：celery_task_{sent,succeeded,failed,retried}_total）
    worker_send_task_events=True,
    task_send_sent_event=True,

    # ── 超时 ──
    task_soft_time_limit=CELERY_TASK_TIMEOUT,   # 触发 SoftTimeLimitExceeded → FAILED
    task_time_limit=CELERY_HARD_TASK_TIMEOUT,   # 硬杀兜底

    # ── 结果 ──
    result_expires=86400,                # result backend 24h 过期（状态权威在 PG）
    timezone="Asia/Shanghai",
    enable_utc=True,

    # ── 路由：agent 任务专用队列（水平扩展时按队列扩 Worker）──
    # 阶段4：RAG 上传索引独立队列（索引吃内存/模型，与 agent 图任务隔离扩缩容）
    task_default_queue="agent",
    task_routes={"tasks.execute_agent": {"queue": "agent"},
                 "tasks.execute_index": {"queue": "rag_index"}},

    # ── Beat 周期任务（P2.4：客服全局维护，60s 兜底扫描）──
    # 幂等（原子条件 UPDATE）：重复调度/多实例并发安全，无需去重键
    beat_schedule={
        "cs-handoff-timeout-scan": {
            "task": "cs.handoff_timeout_scan",
            "schedule": 60.0,
        },
        "cs-confirmation-expiry-scan": {
            "task": "cs.confirmation_expiry_scan",
            "schedule": 60.0,
        },
    },
)
