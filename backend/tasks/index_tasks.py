"""tasks/index_tasks.py — RAG 上传索引 Celery 任务（阶段4 队列化）。

职责划分（对齐 agent_tasks.py 模式）：
- Celery：调度（排队/超时杀），不感知索引内部状态
- 状态权威：TaskState（tasks 表）为业务终态口径；registry + Redis 镜像为
  索引域内部状态（Phase1 Step8 接入，settle 与 TaskState 由本模块统一收口）
- 索引执行：复用 rag_upload._do_index_sync

三套机制边界（Phase2 Step2，与 agent_tasks 同口径）：
- Retry：分类为可重试错误（provider_timeout/rate_limited/provider_error/...）
  → TaskRetryScheduled → 壳层状态复查 → self.retry（退避+jitter）；
  单文档索引 = 一个原子节点，重试整节点重跑（registry duplicate/upsert 吸收）
- Recovery：Worker 死亡 → Step1 stale sweeper（不经过本模块）
- Failure：不可重试（quota_exhausted/validation_error/auth_failed/...）→
  registry settle + TaskState FAILED；retry budget 耗尽 → FAILED(retry_exhausted)

SSE：Worker 与 API 分属不同进程，进度经 Redis 镜像由 API 侧消费。
"""
from __future__ import annotations

from backend.config.tasks import (
    CELERY_MAX_RETRIES,
)
# 顶层导入：impl/壳层 except 子句在异常匹配期解析该名字（无循环依赖，
# admission 包不反向依赖本模块）
from backend.tasks.admission import AdmissionDeferred


def _redis_emit_fn(upload_id: str):
    """Worker 侧事件发射器：仅写 Redis 进度镜像（无进程内队列概念）。"""
    from backend.app.api.routes.rag_upload import _write_progress_redis

    def emit(stage: str, message: str = "", **extra) -> None:
        _write_progress_redis(upload_id, stage, message, **extra)

    return emit


def _index_failure_exit(exc: BaseException, *, emit_fn, upload_id: str,
                        filepath: str, filename: str, kb_id: str,
                        department: str, source: str, batch_id: str | None,
                        upload_elapsed_ms: int | None, was_overwrite: bool,
                        db_task_id: str | None, retries: int,
                        staging_path: str = "") -> None:
    """索引统一失败出口：分类 → 等待重试事件 或 registry 终态收口。

    - retryable 且 budget 未耗尽：只发 uploading 进度（不发终态），抛
      TaskRetryScheduled 由壳层复查后 self.retry
    - 否则：_settle_index_result（registry failed / 清理源文件 / 终态
      error 事件）+ TaskState retry_exhausted 补写，再上抛原始异常
      （Celery FAILURE + signals 补 traceback）
    """
    from backend.app.api.routes.rag_upload import _settle_index_result
    from backend.tasks.error_taxonomy import classify_task_error
    from backend.tasks.retry_policy import (TaskRetryScheduled, compute_delay,
                                            get_policy)

    decision = classify_task_error(exc)
    retries_left = CELERY_MAX_RETRIES - retries
    if decision.retryable and retries_left > 0:
        delay = compute_delay(get_policy("rag_index"), retries)
        emit_fn("uploading",
                f"索引异常（{decision.error_type}），将自动重试"
                f"（第 {retries + 1}/{CELERY_MAX_RETRIES} 次，{delay}s 后）: "
                f"{type(exc).__name__}: {exc}"[:300])
        raise TaskRetryScheduled(
            exc, error_type=decision.error_type, retryable=True,
            delay=delay, retry_count=retries, max_retries=CELERY_MAX_RETRIES)

    # 终态：运行记录 failed / 清理暂存 / 终态 error 事件（发布段失败除外，
    # 正式数据不动——见 _settle_index_result 的 publishing 守卫）
    _settle_index_result(
        upload_id, filepath, filename, source, batch_id, kb_id,
        upload_elapsed_ms, was_overwrite, 0.0,
        result=None, emit_fn=emit_fn, exc=exc, staging_path=staging_path)
    if db_task_id:
        from backend.models.task import TaskStatus
        from backend.services import task_service

        exhausted = decision.retryable  # 分类可重试但 budget 耗尽
        try:
            task_service.update_status(
                db_task_id, TaskStatus.FAILED,  # FAILED 自转换（幂等刷新终态语义）
                progress="重试耗尽，终态收口" if exhausted else
                         f"不可重试（{decision.error_type}），终态收口",
                retry_exhausted=exhausted)
        except Exception:  # noqa: BLE001 — TaskState 补写失败不影响 registry 收口
            from backend.shared.logger import logger

            logger.warning("[IndexTask] %s 终态补写失败", db_task_id,
                           exc_info=True)
    from backend.shared.logger import logger

    logger.error(
        "[IndexTask] %s final failure: error_type=%s retry_exhausted=%s "
        "retry_count=%s/%s", upload_id, decision.error_type,
        decision.retryable and retries_left <= 0, retries, CELERY_MAX_RETRIES)


def execute_index_task_impl(upload_id: str, filepath: str, filename: str,
                            kb_id: str = "policy_general",
                            department: str = "general", source: str = "",
                            batch_id: str | None = None,
                            upload_elapsed_ms: int | None = None,
                            was_overwrite: bool = False,
                            db_task_id: str | None = None,
                            staging_path: str = "",
                            generation: str = "",
                            file_hash: str = "",
                            retries: int = 0) -> dict:
    """索引执行主体（Celery task 与 eager 测试共用的纯函数）。

    成功返回 {"status": terminal, ...}；可重试失败抛 TaskRetryScheduled
    （壳层重投）；终态失败上抛原始异常（Celery FAILURE）。终态事件
    （done/duplicate/error）写 Redis 镜像，供 SSE 轮询通道消费。

    db_task_id（Phase1 Step8 试点）：tasks 表关联行。None = 存量消息/降级
    模式，直接执行不碰 TaskState（行为与历史版本一致）。
    """
    import time as _time

    from backend.app.api.routes.rag_upload import (
        _do_index_sync, _settle_index_result,
    )
    from backend.tasks.index_task_runtime import (
        IndexTaskCancelled, IndexTaskPaused, run_with_task_state,
    )

    emit_fn = _redis_emit_fn(upload_id)
    t0 = _time.time()
    if retries:
        emit_fn("uploading", f"索引重试（第 {retries}/{CELERY_MAX_RETRIES} 次）...")

    def run_index() -> dict:
        # main_loop 传 None：Worker 进程无 asyncio 主循环，
        # _do_index_sync 的 sync_emit 在队列不存在时只写 Redis，不碰 loop。
        # B 阶段候选模式：Worker 只读不可变暂存文件，发布协议统一提交。
        result = _do_index_sync(upload_id, filepath, filename, None,
                                kb_id, department, batch_id=batch_id,
                                staging_path=staging_path,
                                generation=generation,
                                file_hash=file_hash) or {}
        _settle_index_result(
            upload_id, filepath, filename, source, batch_id, kb_id,
            upload_elapsed_ms, was_overwrite, t0,
            result=result, emit_fn=emit_fn, staging_path=staging_path)
        return result

    # Phase1 Step8：TaskState 包装（租约/状态落库/节点边界 pause-cancel，
    # Phase2 Step2 起异常在此层按分类落 error_type）。
    try:
        result = run_with_task_state(db_task_id, upload_id, run_index,
                                     retries=retries)
    except AdmissionDeferred:
        # admission defer 非业务失败（Phase2 Step4）：不进失败出口，
        # 由壳层按 delay countdown 重投（保持 rag_index 队列亲和）
        raise
    except IndexTaskPaused:
        return {"status": "paused", "skipped": True}
    except IndexTaskCancelled:
        return {"status": "cancelled", "skipped": True}
    except Exception as e:
        # 统一失败出口（SoftTimeLimit / provider 错误 / 校验错误同一条路）：
        # 可重试 → 抛 TaskRetryScheduled（壳层复查后 self.retry）；
        # 终态 → registry settle + TaskState 补写后 re-raise（Celery FAILURE）
        _index_failure_exit(
            e, emit_fn=emit_fn, upload_id=upload_id, filepath=filepath,
            filename=filename, kb_id=kb_id, department=department,
            source=source, batch_id=batch_id,
            upload_elapsed_ms=upload_elapsed_ms, was_overwrite=was_overwrite,
            db_task_id=db_task_id, retries=retries,
            staging_path=staging_path)
        raise  # 仅终态路径可达：re-raise 原始异常

    if (result or {}).get("skipped"):
        return result
    terminal = (result or {}).get("terminal", "done")
    doc = (result or {}).get("doc") or {}
    return {"status": terminal,
            "trace_id": (result or {}).get("trace_id") or "",
            "doc_id": doc.get("doc_id", "") if isinstance(doc, dict) else ""}


def _register_task():
    """惰性注册：celery 未安装时允许模块被非 Worker 进程安全 import。"""
    from backend.tasks.celery_app import celery_app

    @celery_app.task(
        bind=True,
        name="tasks.execute_index",
        acks_late=True,
        # Phase2 Step2：无差别 autoretry_for=(Exception,) 已移除——
        # 重试由分类决策经 TaskRetryScheduled → 壳层状态复查 → self.retry
        max_retries=CELERY_MAX_RETRIES,
    )
    def execute_index_task(self, upload_id: str, filepath: str, filename: str,
                           kb_id: str = "policy_general",
                           department: str = "general", source: str = "",
                           batch_id: str | None = None,
                           upload_elapsed_ms: int | None = None,
                           was_overwrite: bool = False,
                           db_task_id: str | None = None,
                           staging_path: str = "",
                           generation: str = "",
                           file_hash: str = "") -> dict:
        from backend.tasks.retry_policy import TaskRetryScheduled

        try:
            return execute_index_task_impl(
                upload_id, filepath, filename, kb_id=kb_id,
                department=department, source=source, batch_id=batch_id,
                upload_elapsed_ms=upload_elapsed_ms,
                was_overwrite=was_overwrite,
                db_task_id=db_task_id,
                staging_path=staging_path, generation=generation,
                file_hash=file_hash,
                retries=self.request.retries)
        except AdmissionDeferred as deferred:
            # Phase2 Step4：admission 满载延迟准入——apply_async 新消息
            # countdown 重投（不走 self.retry：defer 不消耗业务 retry
            # 计数，budget 由 admission defer key 独立管理）；queue 经
            # QueueRouter workflow 亲和，不自拼队列名
            from backend.shared.logger import logger
            from backend.tasks.queue_router import resolve_for_workflow

            route = resolve_for_workflow("rag_index")
            logger.warning(
                "[IndexTask] %s admission deferred，%.1fs 后重投 %s",
                upload_id, deferred.delay_seconds, route.physical_queue)
            execute_index_task.apply_async(
                kwargs=dict(
                    upload_id=upload_id, filepath=filepath,
                    filename=filename, kb_id=kb_id, department=department,
                    source=source, batch_id=batch_id,
                    upload_elapsed_ms=upload_elapsed_ms,
                    was_overwrite=was_overwrite, db_task_id=db_task_id,
                    staging_path=staging_path, generation=generation,
                    file_hash=file_hash),
                queue=route.physical_queue,
                countdown=deferred.delay_seconds)
            return {"status": "ADMISSION_DEFERRED", "skipped": True}
        except TaskRetryScheduled as scheduled:
            if db_task_id:
                # §十一：retry 入队前最终状态复查（CANCELLED/PAUSED/终态
                # 不被 retry 唤醒）；存量消息（无 TaskState 行）直接重试
                from backend.models.task import TaskStatus
                from backend.services import task_service
                from backend.tasks.queue_router import (log_route,
                                                        resolve_for_task)

                record = task_service.get_task(db_task_id)
                if record is None or record.status != TaskStatus.FAILED:
                    from backend.shared.logger import logger

                    logger.warning(
                        "[IndexTask] %s retry recheck: status=%s，放弃重投",
                        db_task_id,
                        record.status.value if record else "MISSING")
                    return {"status": "RETRY_CANCELLED", "skipped": True}
                # Step3：重投队列显式经 QueueRouter（rag_index 亲和 +
                # 配置变化跟随新 binding）
                route = resolve_for_task(record)
                log_route(route, dispatch_type="retry", task_id=db_task_id,
                          celery_task_name="tasks.execute_index",
                          previous_queue=record.queue)
                raise self.retry(exc=scheduled.original,
                                 countdown=scheduled.delay,
                                 queue=route.physical_queue)
            raise self.retry(exc=scheduled.original,
                             countdown=scheduled.delay)

    return execute_index_task


execute_index_task = _register_task()
