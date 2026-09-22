"""services/task_state.py — 业务任务状态门面（Phase1 Task Runtime Step1）。

TaskManager = TaskState（agent_memory.tasks 行）的统一读写入口：
- create / get / mark_running / mark_paused / mark_success / mark_failed /
  mark_cancelled 七个最小操作
- 状态流转合法性由持久层强制（task_service.update_status 内置
  TaskStatus 白名单校验，非法跳转抛 IllegalTaskTransition）——本门面
  不重复实现规则，只做语义化命名与字段口径收敛

与其他两个"manager"的分工（避免混用）：
- task_service（DAO）：裸 SQL 读写，不懂业务语义
- task_state.TaskManager（本模块）：状态语义门面，业务方优先用这个
- tasks/task_manager（编排门面）：入队/resume/控制标志/事件广播

error_code 口径：映射 tasks.error_type 列（异常类名/错误分类码），不另存列。
workflow 口径：映射 graph_name 列（TaskRecord.workflow 别名），不另存列。
"""
from __future__ import annotations

from backend.models.task import TaskRecord, TaskStatus
from backend.services import task_service


def _publish_status(task_id: str, status: TaskStatus, progress: str = "") -> None:
    """发布 task_status 事件（Phase1 Step7 SSE：状态事实源变更即广播）。

    广播失败不影响落库（publish_event 内部 fire-and-forget）；import 失败
    同样静默——事件通道是 best-effort，DB 才是事实源。
    """
    try:
        from backend.tasks.task_manager import publish_event

        publish_event(task_id, "task_status", status=status.value,
                      progress=progress)
    except Exception:  # noqa: BLE001 — 事件是旁路，不阻塞状态落库
        pass


class TaskManager:
    """统一业务任务状态机门面（所有写操作经状态机校验）。"""

    # ── 读 ────────────────────────────────────────────────
    @staticmethod
    def get(task_id: str) -> TaskRecord | None:
        """按 task_id 读取任务（内部/Worker 用；API 层须走 user 维度接口）。"""
        return task_service.get_task(task_id)

    # ── 写 ────────────────────────────────────────────────
    @staticmethod
    def create(user_id: str, query: str, *, tenant_id: str = "default",
               graph_name: str = "main", conversation_id: str = "",
               trace_id: str = "", biz_type: str = "", biz_id: str = "",
               parent_task_id: str = "",
               extra_input: dict | None = None) -> TaskRecord:
        """创建 PENDING 任务（thread_id = task-{task_id}，checkpoint 定位键）。

        extra_input：执行器重投所需业务参数（如 rag_index 的索引 kwargs），
        落 input JSONB——resume 执行器路由依赖。
        """
        return task_service.create_task(
            user_id, query, tenant_id=tenant_id, graph_name=graph_name,
            conversation_id=conversation_id, trace_id=trace_id,
            biz_type=biz_type, biz_id=biz_id, parent_task_id=parent_task_id,
            extra_input=extra_input)

    @staticmethod
    def mark_running(task_id: str, *, progress: str = "",
                     checkpoint_id: str | None = None,
                     worker: str | None = None) -> None:
        """PENDING/PAUSED → RUNNING（started_at 首次启动才落）。"""
        task_service.update_status(
            task_id, TaskStatus.RUNNING, progress=progress,
            checkpoint_id=checkpoint_id, worker=worker)
        _publish_status(task_id, TaskStatus.RUNNING, progress)

    @staticmethod
    def mark_paused(task_id: str, *, message: str = "用户暂停") -> None:
        """RUNNING → PAUSED（checkpoint 已由执行器保留）。"""
        task_service.update_status(
            task_id, TaskStatus.PAUSED, error_message="", progress=message)
        _publish_status(task_id, TaskStatus.PAUSED, message)

    @staticmethod
    def mark_success(task_id: str, *, output: dict | None = None,
                     progress: str = "执行完成",
                     duration_ms: int | None = None) -> None:
        """RUNNING → SUCCESS（finished_at/duration 自动补算）。"""
        task_service.update_status(
            task_id, TaskStatus.SUCCESS, progress=progress, output=output,
            duration_ms=duration_ms)
        _publish_status(task_id, TaskStatus.SUCCESS, progress)

    @staticmethod
    def mark_failed(task_id: str, *, error_message: str,
                    error_code: str = "", progress: str = "",
                    traceback_text: str | None = None) -> None:
        """RUNNING → FAILED（error_code 落 error_type 列，任务中心归因筛选）。"""
        task_service.update_status(
            task_id, TaskStatus.FAILED, error_message=error_message[:2000],
            error_type=error_code or None, progress=progress,
            traceback_text=traceback_text)
        _publish_status(task_id, TaskStatus.FAILED, progress or error_message)

    @staticmethod
    def mark_cancelled(task_id: str, *, message: str = "用户取消") -> None:
        """RUNNING/PAUSED/PENDING → CANCELLED（checkpoint 与已完成结果保留）。"""
        task_service.update_status(
            task_id, TaskStatus.CANCELLED, error_message="", progress=message)
        _publish_status(task_id, TaskStatus.CANCELLED, message)

    @staticmethod
    def requeue_failed(task_id: str, *,
                       progress: str = "重试回队（从 checkpoint 续跑）") -> TaskRecord:
        """FAILED 显式回 PENDING（Celery 重投/重试路径的统一入口）。

        状态机禁止 FAILED→RUNNING 直跳——重投 Worker 认领租约前必须先
        走这里（幂等：已 PENDING 时记录状态不匹配直接原样返回，无跳转）。
        """
        record = task_service.get_task(task_id)
        if record is not None and record.status == TaskStatus.FAILED:
            task_service.update_status(task_id, TaskStatus.PENDING,
                                       progress=progress)
            record = task_service.get_task(task_id)
        return record  # type: ignore[return-value]
