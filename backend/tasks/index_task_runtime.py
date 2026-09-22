"""tasks/index_task_runtime.py — rag_index 任务的 TaskState 接入层（Phase1 Step8 试点）。

目标：把 rag_index 队列（execute_index）纳入统一任务状态机体系，消灭
"registry + Redis 镜像" 与 tasks 表的双状态源。原则：**Runtime 层收口，
indexer 零侵入**。

接入语义（对齐 Phase1 规格"当前原子操作是否可中断，按现有能力处理"）：
- 单文档索引 = 一个原子节点（解析→元数据→嵌入→向量写入→settle 不可拆）。
  pause/cancel 标志只在节点边界生效：节点开始前（未开跑即停）与整个节点
  （含 settle）完成后。中途请求不会强杀正在进行的索引。
- 幂等：resume/重试重跑原子节点，由既有 registry duplicate 检测与向量
  upsert 语义吸收（已完成文档不重复入库）。
- checkpoint：节点完成后 append_checkpoint（agent_checkpoints 行，记录
  chunks/doc_id/terminal 快照），LangGraph 级 checkpoint 对单节点任务不适用。
- 向后兼容：存量队列消息无 db_task_id → 直接执行，不碰 tasks 表（行为不变）。
- 可用性优先：dispatch 侧 tasks 行创建失败只记日志降级为无 TaskState 模式，
  绝不阻断上传索引主链路。
"""
from __future__ import annotations

from backend.models.task import TaskStatus
from backend.shared.logger import logger


class IndexTaskPaused(Exception):
    """索引任务被暂停（节点边界语义，settle 已完成，结果保留）。"""


class IndexTaskCancelled(Exception):
    """索引任务被取消（节点边界语义，settle 已完成，结果保留）。"""


# ═══════════════════════════════════════════════════
# dispatch 侧（API 进程）：创建 tasks 行
# ═══════════════════════════════════════════════════

def create_index_task_record(upload_id: str, filename: str, *,
                             kb_id: str = "", tenant_id: str = "default",
                             user_id: str = "system",
                             conversation_id: str = "",
                             index_kwargs: dict | None = None) -> str | None:
    """为索引任务创建 PENDING tasks 行，返回 task_id；失败降级返回 None。

    index_kwargs（Celery 消息原文）持久化进 tasks.input——PAUSED 后 resume
    重投时按 graph_name 路由回 rag_index 队列必需（实机演练 2026-09-23：
    缺它会导致 resume 误走 agent 队列 → EmptyInputError）。
    """
    try:
        from backend.services.task_state import TaskManager

        record = TaskManager.create(
            user_id, f"索引文档: {filename}", tenant_id=tenant_id,
            graph_name="rag_index", conversation_id=conversation_id,
            biz_type="rag_index", biz_id=upload_id,
            extra_input={"index_kwargs": index_kwargs or {}})
        return record.id
    except Exception:
        logger.warning("[IndexTaskRuntime] tasks 行创建失败，降级无 TaskState "
                       "模式: upload_id=%s", upload_id, exc_info=True)
        return None


def redispatch_index_task(task_id: str) -> str | None:
    """按 tasks.input 里的原始 kwargs 重投 rag_index 队列（resume 专用）。

    返回 celery async result id；task 行不存在或缺 index_kwargs 时 None
    （调用方回落通用入队或报错）。
    """
    from backend.services import task_service
    from backend.tasks.index_tasks import execute_index_task
    from backend.config.tasks import CELERY_RAG_INDEX_QUEUE

    record = task_service.get_task(task_id)
    if record is None:
        return None
    index_kwargs = (record.input or {}).get("index_kwargs") or {}
    if not index_kwargs:
        logger.error("[IndexTaskRuntime] %s 缺 index_kwargs，无法重投", task_id)
        return None
    async_result = execute_index_task.apply_async(
        kwargs={**index_kwargs, "db_task_id": task_id},
        queue=CELERY_RAG_INDEX_QUEUE)
    task_service.mark_queued(task_id, getattr(async_result, "id", ""),
                             queue=CELERY_RAG_INDEX_QUEUE)
    logger.info("[IndexTaskRuntime] %s redispatched to rag_index (%s)",
                task_id, str(getattr(async_result, "id", ""))[:8])
    return getattr(async_result, "id", None)


# ═══════════════════════════════════════════════════
# Worker 侧：租约 + 状态落库 + 节点边界控制
# ═══════════════════════════════════════════════════

def _flags(task_id: str) -> tuple[bool, bool]:
    from backend.tasks.task_manager import is_cancel_requested, is_pause_requested

    return is_cancel_requested(task_id), is_pause_requested(task_id)


def run_with_task_state(db_task_id: str | None, upload_id: str,
                        run_index, *, retries: int = 0) -> dict:
    """索引原子节点的 TaskState 包装（execute_index_task_impl 专用）。

    run_index: 无参 callable，执行单文档索引（含 settle），返回
    {"status": terminal, ...} 形态结果。本函数负责：
      1. 无 db_task_id（存量消息）→ 直接执行，行为不变
      2. 显式状态短路（Phase2 Step1）：SUCCESS/CANCELLED/PAUSED/WAITING_USER
         一律不执行——redelivery/恢复重投不得唤醒终态与暂停任务
      3. FAILED（Celery 重投）→ 显式回 PENDING 再抢租约
      4. 租约认领（max executor=1）+ 心跳线程（长原子节点内维持活性）；
         认领失败 skip
      5. 节点边界控制：开始前 / 整节点（含 settle）完成后轮询标志
      6. 终态落库（SUCCESS/FAILED/PAUSED/CANCELLED）+ checkpoint 快照，
         全部带 execution_id fencing——租约被接管的旧 Worker 写不进去
      7. 失败按 runtime 分类落 error_type（Phase2 Step2），可重试但
         budget 耗尽时补 retry_exhausted（终态细分由 _index_failure_exit 完成）
    """
    if not db_task_id:
        return run_index()

    from backend.config.tasks import CELERY_MAX_RETRIES
    from backend.models.task import TaskLeaseLost
    from backend.services import task_service
    from backend.services.task_state import TaskManager
    from backend.tasks.error_taxonomy import classify_task_error
    from backend.tasks.execution_context import clear_execution, set_execution
    from backend.tasks.lease_heartbeat import LeaseHeartbeat

    def _lease_lost_result(reason: str) -> dict:
        return {"status": "error", "error": reason, "skipped": True,
                "lease_lost": True}

    record = TaskManager.requeue_failed(db_task_id)
    if record is None:
        logger.warning("[IndexTaskRuntime] %s tasks 行不存在，直接执行", db_task_id)
        return run_index()
    if record.status == TaskStatus.CANCELLED:
        logger.info("[IndexTaskRuntime] %s already cancelled, skip", db_task_id)
        return {"status": "error", "error": "cancelled", "skipped": True}
    if record.status == TaskStatus.SUCCESS:
        # 终态收到重复消息（redelivery/恢复重投晚到）：NO-OP，不重复索引
        logger.info("[IndexTaskRuntime] %s already succeeded, no-op", db_task_id)
        return {"status": "done", "skipped": True}
    if record.status in (TaskStatus.PAUSED, TaskStatus.WAITING_USER):
        # 暂停中的任务被重投（消息早于 pause 请求）：不执行，等待 resume
        logger.info("[IndexTaskRuntime] %s paused, skip execution", db_task_id)
        return {"status": "error", "error": "paused", "skipped": True}

    lease_id = task_service.try_acquire_lease(db_task_id, worker=None)
    if not lease_id:
        logger.warning("[IndexTaskRuntime] %s lease held elsewhere, skip", db_task_id)
        return {"status": "error", "error": "running_elsewhere", "skipped": True}

    hb = LeaseHeartbeat(db_task_id, lease_id)
    hb.start()
    ctx_token = set_execution(db_task_id, lease_id)
    try:
        # ── 节点开始前：标志已置位则不开跑（fencing 写）──
        cancelled, paused = _flags(db_task_id)
        if cancelled:
            TaskManager.mark_cancelled(db_task_id, message="用户取消（执行前拦截）",
                                       execution_id=lease_id)
            return {"status": "error", "error": "cancelled", "skipped": True}
        if paused:
            TaskManager.mark_paused(db_task_id, message="用户暂停（执行前拦截）",
                                    execution_id=lease_id)
            return {"status": "error", "error": "paused", "skipped": True}

        TaskManager.mark_running(db_task_id, progress=f"索引文档 {upload_id}",
                                 execution_id=lease_id)
        try:
            result = run_index()
        except TaskLeaseLost:
            # 心跳已在节点内判定丢失（罕见：run_index 内部经 fence 写触发）
            return _lease_lost_result("lease_lost")
        except Exception as e:
            # Phase2 Step2：分类落 error_type（不再落异常类名）；
            # 可重试但 budget 耗尽 → retry_exhausted 终态细分
            decision = classify_task_error(e)
            exhausted = decision.retryable and retries >= CELERY_MAX_RETRIES
            try:
                TaskManager.mark_failed(
                    db_task_id, error_message=str(e)[:2000],
                    error_code=decision.error_type,
                    progress=("重试耗尽" if exhausted else
                              ("等待重试" if decision.retryable
                               else f"不可重试（{decision.error_type}）")),
                    execution_id=lease_id,
                    retry_exhausted=True if exhausted else None)
            except TaskLeaseLost:
                logger.warning("[IndexTaskRuntime] %s fencing 拒绝 FAILED 写"
                               "（租约已被接管）", db_task_id)
            raise

        # ── 节点完成（settle 已做，结果已持久）：fencing 写 + 标志检查 ──
        try:
            if not task_service.update_progress(db_task_id, "index_document",
                                                progress="索引节点完成",
                                                execution_id=lease_id):
                return _lease_lost_result("lease_lost")
            if not task_service.append_checkpoint(db_task_id, "index_document", {
                "upload_id": upload_id,
                "terminal": (result or {}).get("terminal", ""),
                "doc_id": (result or {}).get("doc", {}).get("doc_id", "")
                if isinstance((result or {}).get("doc"), dict) else "",
            }, execution_id=lease_id):
                return _lease_lost_result("lease_lost")
        except TaskLeaseLost:
            return _lease_lost_result("lease_lost")

        cancelled, paused = _flags(db_task_id)
        if cancelled:
            TaskManager.mark_cancelled(db_task_id, message="用户取消（索引完成，结果保留）",
                                       execution_id=lease_id)
            raise IndexTaskCancelled(db_task_id)
        if paused:
            TaskManager.mark_paused(db_task_id, message="用户暂停（索引完成，结果保留）",
                                    execution_id=lease_id)
            raise IndexTaskPaused(db_task_id)

        terminal = (result or {}).get("terminal", "done")
        try:
            TaskManager.mark_success(
                db_task_id, output={"terminal": terminal,
                                    "doc_id": (result or {}).get("doc", {}).get("doc_id", "")
                                    if isinstance((result or {}).get("doc"), dict) else ""},
                progress=f"索引完成（{terminal}）", execution_id=lease_id)
        except TaskLeaseLost:
            # 索引本体已完成（registry/向量库已持久），仅状态写被新 owner 顶替；
            # 恢复链会重跑本节点，由 registry duplicate/upsert 语义吸收
            logger.warning("[IndexTaskRuntime] %s fencing 拒绝 SUCCESS 写"
                           "（租约已被接管）", db_task_id)
            return _lease_lost_result("lease_lost")
        return result
    finally:
        hb.stop()
        clear_execution(ctx_token)
