"""tasks/celery_app.py — Celery 应用工厂。

设计原则：
- Redis 同时作 broker 与 result backend（与业务缓存分库，默认 /1）
- 全部配置来自环境变量（backend.config.tasks），无硬编码
- 只调度不承载 Agent 状态：任务 payload 仅 task_id（json 可序列化）
- acks_late + prefetch=1：Worker 宕机任务自动回队；多实例公平消费
"""
import asyncio
import os

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init

from backend.config.tasks import (
    CELERY_BROKER_URL,
    CELERY_HARD_TASK_TIMEOUT,
    CELERY_METADATA_SHADOW_MAX_RETRIES,
    CELERY_METADATA_SHADOW_QUEUE,
    CELERY_METADATA_SHADOW_TASK_TIMEOUT,
    CELERY_RESULT_BACKEND,
    CELERY_RETRY_BACKOFF,
    CELERY_RETRY_BACKOFF_MAX,
    CELERY_TASK_TIMEOUT,
    TASK_ZOMBIE_RECONCILE_INTERVAL,
)
from backend.shared.logger import logger

celery_app = Celery(
    "agent_tasks",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["backend.tasks.agent_tasks",     # Worker 启动自动注册任务模块
             "backend.tasks.index_tasks",     # 阶段4：RAG 上传索引队列化任务
             "backend.tasks.metadata_shadow_tasks",  # 元数据影子隔离队列
             "backend.tasks.cs_maintenance_tasks",  # P2.4：客服全局维护（beat）
             "backend.tasks.cs_qa_tasks",  # 批次D：客服质检每日报表（beat）
             "backend.tasks.task_maintenance_tasks",  # B5：僵尸任务 reconcile（beat）
             "backend.tasks.model_health_tasks",  # 治理：模型健康周期探测（beat）
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
                 "tasks.execute_index": {"queue": "rag_index"},
                 "tasks.execute_metadata_shadow": {
                     "queue": CELERY_METADATA_SHADOW_QUEUE,
                 }},

    # 影子任务有更短的独立超时；任务自身装饰器会使用同一重试退避策略。
    task_annotations={
        "tasks.execute_metadata_shadow": {
            "soft_time_limit": CELERY_METADATA_SHADOW_TASK_TIMEOUT,
            "time_limit": CELERY_METADATA_SHADOW_TASK_TIMEOUT + 30,
            "max_retries": CELERY_METADATA_SHADOW_MAX_RETRIES,
            "retry_backoff": CELERY_RETRY_BACKOFF,
            "retry_backoff_max": CELERY_RETRY_BACKOFF_MAX,
        },
    },

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
        "cs-event-outbox-compensation": {
            "task": "cs.event_outbox_compensation",
            "schedule": 15.0,
        },
        # 批次D（2026-09-22）：客服质检每日报表。每日 06:10 UTC 聚合昨日
        # 指标（幂等覆盖 qa_daily_reports）；失败自动重试（最多 3 次）。
        "cs-qa-daily-report": {
            "task": "cs.qa_daily_report",
            "schedule": crontab(hour=6, minute=10),
        },
        # B5（2026-09-21 高并发审查）：僵尸 RUNNING 任务定期收尸。
        # 阈值与间隔均可经 env 覆盖（TASK_ZOMBIE_*，见 backend/config/tasks.py）
        "tasks-zombie-reconcile": {
            "task": "tasks.zombie_reconcile",
            "schedule": float(TASK_ZOMBIE_RECONCILE_INTERVAL),
        },
        # 治理改造（2026-09-22）：模型健康周期探测 → llm_model_health 缓存。
        # 页面只读缓存；间隔经 env MODEL_HEALTH_SCAN_INTERVAL 可调（默认 300s）。
        "model-health-scan": {
            "task": "model.health_scan",
            "schedule": float(os.getenv("MODEL_HEALTH_SCAN_INTERVAL", "300")),
        },
    },
)


def refresh_worker_model_registry() -> bool:
    """在 Worker 进程内加载数据库模型覆盖层。

    app 和 rag-service 的启动流程会主动刷新注册表，但 Celery prefork
    子进程有独立的 Python 内存。若不在这里刷新，索引任务会退回空的
    env/code-default 配置，进而把管理端已经保存的 OCR/Embedding 误判为未配置。
    """
    try:
        from backend.infra.llm.registry_store import refresh_registry

        loaded = bool(asyncio.run(refresh_registry()))
        if not loaded:
            logger.warning("[Worker] 模型注册表未加载，保留现有进程内配置")
        return loaded
    except Exception:
        logger.warning("[Worker] 模型注册表刷新失败，任务将使用上次已知配置", exc_info=True)
        return False


@worker_process_init.connect(weak=False)
def _on_worker_process_init(**_kwargs) -> None:
    """prefork 子进程启动时建立自己的模型配置快照。"""
    refresh_worker_model_registry()
