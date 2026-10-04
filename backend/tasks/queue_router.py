"""tasks/queue_router.py — 队列路由唯一事实源（Phase2 Step3）。

职责边界（Step3 = 路由规则统一，不做资源隔离）：
- workflow / celery task name → logical workload class → physical Celery queue
- 所有投递路径（initial / retry / resume / recovery / admin_retry / beat）
  一律经本模块 resolve，禁止业务代码自拼 queue 字符串

不做的事（后续 Step 边界）：
- 并发/准入/优先级/租户限流（Step4 Admission 已收口于 tasks/admission/）
- Worker 拓扑 / concurrency / prefetch（Step5：docker-compose 只声明
  哪个 worker 消费哪些物理队列，本模块仍是唯一业务投递决策源）
- 幂等语义（Step6）

Fail-closed 原则：未登记 workflow 抛 QueueRoutingError 阻止入队——
rag_index 曾被误投 agent 队列的根源模式就是"未知 → 默认 agent"，
本模块从架构上消灭该模式。运维逃生门：QUEUE_ROUTING_UNKNOWN_FALLBACK
显式设为某个物理队列名时，未登记 workflow 降级 legacy_fallback（必打
warning，可审计）；默认空 = 严格 fail-closed。

单一事实源（G2）：
- celery_app.task_routes 与 beat_schedule 的 options.queue 均从本模块
  派生函数生成，禁止在 celery_app.py 手写第二份映射。
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.config.tasks import (
    CELERY_AGENT_QUEUE,
    CELERY_MAINTENANCE_QUEUE,
    CELERY_METADATA_SHADOW_QUEUE,
    CELERY_RAG_INDEX_QUEUE,
    CELERY_REPORT_QUEUE,
    QUEUE_ROUTING_UNKNOWN_FALLBACK,
)
from backend.shared.logger import logger

# 路由版本：binding 语义变更（logical↔physical 映射重排）时递增，
# 进 trace 便于回放"这条消息当时按哪版规则路由"。
ROUTING_VERSION = "1"

# 未登记 workflow 的降级队列（空 = fail-closed，默认关闭）
_UNKNOWN_FALLBACK_QUEUE = QUEUE_ROUTING_UNKNOWN_FALLBACK


class QueueRoutingError(Exception):
    """未登记 workflow / celery task 的路由请求（fail-closed，阻止入队）。"""


@dataclass(frozen=True)
class QueueRoute:
    """一次路由决策的完整快照（进日志/trace，不作存储）。"""

    workflow: str              # graph_name 或 celery task name
    workload_class: str        # logical queue（workload 分类）
    physical_queue: str        # 物理 Celery 队列（消费边界）
    routing_reason: str        # workflow_binding / beat_binding / legacy_fallback
    routing_source: str = "registry"
    routing_version: str = ROUTING_VERSION


# ═══════════════════════════════════════════════════
# Registry（模块级常量：单测可 monkeypatch 模拟配置变化，Case J）
# ═══════════════════════════════════════════════════

# logical workload class → physical queue（Phase2 Step5：五 workload 五队列，
# 每个物理队列由专属 worker 池消费——资源隔离，互不挤占并发槽）。
_LOGICAL_QUEUES: dict[str, str] = {
    "interactive_agent": CELERY_AGENT_QUEUE,
    "rag_index": CELERY_RAG_INDEX_QUEUE,
    "metadata_shadow": CELERY_METADATA_SHADOW_QUEUE,
    "report": CELERY_REPORT_QUEUE,
    "maintenance": CELERY_MAINTENANCE_QUEUE,
}

# workflow（= tasks.graph_name，Phase1 口径单一事实源）→ (workload_class, reason)
# 未登记的 graph_name 一律 QueueRoutingError（case I）。
_WORKFLOW_ROUTES: dict[str, tuple[str, str]] = {
    "main": ("interactive_agent", "workflow_binding"),
    "rag_index": ("rag_index", "workflow_binding"),
}

# 执行型 Celery task name → (workload_class, reason)。
# initial / retry 的 self.retry 重投均按此表显式取 queue。
_CELERY_TASK_ROUTES: dict[str, tuple[str, str]] = {
    "tasks.execute_agent": ("interactive_agent", "workflow_binding"),
    "tasks.execute_index": ("rag_index", "workflow_binding"),
    "tasks.reindex_document": ("rag_index", "workflow_binding"),
    "tasks.execute_metadata_shadow": ("metadata_shadow", "workflow_binding"),
}

# beat / 手动维护任务 → workload_class（全部显式登记，禁止依赖 default queue）。
_BEAT_TASK_ROUTES: dict[str, tuple[str, str]] = {
    "rag.index_reconcile": ("maintenance", "beat_binding"),
    "rag.parsing_timeout_watchdog": ("maintenance", "beat_binding"),
    "cs.handoff_timeout_scan": ("maintenance", "beat_binding"),
    "cs.confirmation_expiry_scan": ("maintenance", "beat_binding"),
    "cs.event_outbox_compensation": ("maintenance", "beat_binding"),
    "cs.qa_daily_report": ("report", "beat_binding"),
    "memory.daily_decay": ("maintenance", "beat_binding"),
    "tasks.pending_recovery": ("maintenance", "beat_binding"),
    "tasks.zombie_reconcile": ("maintenance", "beat_binding"),
    "tasks.stale_execution_recovery": ("maintenance", "beat_binding"),
    "tasks.idempotency_retention": ("maintenance", "beat_binding"),
    "travel.booking_recovery_scan": ("maintenance", "beat_binding"),
    "tasks.side_effect_probe": ("maintenance", "beat_binding"),
    "model.health_scan": ("maintenance", "beat_binding"),
    "model.health_check_one": ("maintenance", "beat_binding"),
    "budget.reconciliation_check": ("maintenance", "beat_binding"),
    "prompts.poll_github_eval": ("maintenance", "prompt_eval_polling"),
}

# dispatch_type 合法词表（observability：每次路由决策必带）
DISPATCH_TYPES = ("initial", "retry", "resume", "recovery",
                  "admin_retry", "beat")


# ═══════════════════════════════════════════════════
# Resolve API（全部纯函数：同配置版本下结果确定性稳定）
# ═══════════════════════════════════════════════════

def _resolve_class(workload_class: str, reason: str, key: str) -> QueueRoute:
    physical = _LOGICAL_QUEUES.get(workload_class)
    if physical is None:
        # 登记了 workflow 但 workload_class 拼错 = registry 自身缺陷，fast-fail
        raise QueueRoutingError(
            f"workload_class 未登记物理队列: {workload_class!r} (key={key})")
    return QueueRoute(workflow=key, workload_class=workload_class,
                      physical_queue=physical, routing_reason=reason)


def resolve_for_workflow(workflow: str) -> QueueRoute:
    """graph_name → QueueRoute。未登记 → QueueRoutingError（不偷偷投 agent）。"""
    binding = _WORKFLOW_ROUTES.get(workflow)
    if binding is None:
        return _unknown_fallback(workflow)
    return _resolve_class(binding[0], binding[1], workflow)


def resolve_for_task(record) -> QueueRoute:
    """TaskRecord（或含 workflow 键的 dict）→ QueueRoute。

    record.workflow 是 graph_name 的口径别名（models/task.py），本模块
    不读 tasks.queue——那是上次投递的物理快照，不是配置源。
    """
    workflow = getattr(record, "workflow", None) or (
        record.get("workflow", "") if isinstance(record, dict) else "")
    return resolve_for_workflow(workflow)


def resolve_for_celery_task(task_name: str) -> QueueRoute:
    """Celery task name → QueueRoute（执行型 + beat/maintenance 统一查表）。"""
    binding = _CELERY_TASK_ROUTES.get(task_name) or _BEAT_TASK_ROUTES.get(task_name)
    if binding is None:
        return _unknown_fallback(task_name)
    return _resolve_class(binding[0], binding[1], task_name)


def _unknown_fallback(key: str) -> QueueRoute:
    if _UNKNOWN_FALLBACK_QUEUE:
        logger.warning("[QueueRouter] 未登记路由 %r 降级 legacy_fallback → %s "
                       "（QUEUE_ROUTING_UNKNOWN_FALLBACK 已开启，请尽快登记）",
                       key, _UNKNOWN_FALLBACK_QUEUE)
        return QueueRoute(workflow=key, workload_class="unknown",
                          physical_queue=_UNKNOWN_FALLBACK_QUEUE,
                          routing_reason="legacy_fallback")
    raise QueueRoutingError(
        f"workflow/task 未登记队列路由: {key!r}（fail-closed，"
        f"请在 queue_router registry 登记后再入队）")


# ═══════════════════════════════════════════════════
# 派生出口（celery_app 消费；G2：派生量回写 = 违规）
# ═══════════════════════════════════════════════════

def celery_task_routes() -> dict[str, dict[str, str]]:
    """派生 Celery task_routes（执行型 + beat 任务全覆盖）。"""
    routes: dict[str, dict[str, str]] = {}
    for table in (_CELERY_TASK_ROUTES, _BEAT_TASK_ROUTES):
        for name, (workload_class, _reason) in table.items():
            routes[name] = {"queue": _LOGICAL_QUEUES[workload_class]}
    return routes


def beat_queue(task_name: str) -> str:
    """beat_schedule 单项的物理队列（celery_app 构造期调用）。"""
    return resolve_for_celery_task(task_name).physical_queue


# ═══════════════════════════════════════════════════
# Observability：每次投递决策的结构化轨迹
# ═══════════════════════════════════════════════════

def log_route(route: QueueRoute, *, dispatch_type: str,
              task_id: str = "", celery_task_name: str = "",
              previous_queue: str = "") -> None:
    """统一路由观测行。retry/resume/recovery 类必传 dispatch_type。

    previous_queue 非空且 ≠ 本次解析结果 = 配置映射已变化（tasks.queue
    是旧快照，以当前 registry 为新执行目标）——单独 warning 便于审计。
    """
    if dispatch_type not in DISPATCH_TYPES:
        raise QueueRoutingError(f"非法 dispatch_type: {dispatch_type!r}")
    logger.info(
        "[QueueRouter] route task=%s workflow=%s celery_task=%s "
        "logical=%s physical=%s reason=%s source=%s version=%s dispatch=%s",
        task_id or "-", route.workflow, celery_task_name or "-",
        route.workload_class, route.physical_queue, route.routing_reason,
        route.routing_source, route.routing_version, dispatch_type)
    if previous_queue and previous_queue != route.physical_queue:
        logger.warning(
            "[QueueRouter] queue binding changed task=%s previous_queue=%s "
            "resolved_queue=%s dispatch=%s（以当前 registry 为新执行目标）",
            task_id or "-", previous_queue, route.physical_queue, dispatch_type)
