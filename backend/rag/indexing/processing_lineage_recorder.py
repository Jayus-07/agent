"""处理运行血缘的容错写入协调器。

索引主链路不应因为观测库短暂不可用而失败。本模块负责把领域状态机
快照写入仓储，并将仓储异常降级为日志；真正的解析、抽取和向量写入
仍由调用方负责。
"""

from __future__ import annotations

import time
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
import os
from typing import Any

from backend.rag.indexing.processing_lineage import (
    ModelIdentity,
    ProcessingRunContext,
    StageStatus,
)
from backend.shared.logger import logger


_LINEAGE_WRITE_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(int(os.getenv("RAG_LINEAGE_WRITE_WORKERS", "4")), 1),
    thread_name_prefix="rag-lineage-writer",
)


class ProcessingRunRecorder:
    """把一个处理运行的状态变化幂等写入血缘仓储。"""

    def __init__(self, context: ProcessingRunContext, repository: Any = None) -> None:
        self.context = context
        self.repository = repository
        self._last_write = None

    def _repository(self):
        return self.repository

    def _write(self, action: str, callback) -> None:
        if self.repository is None:
            return
        if bool(getattr(self.repository, "non_blocking", False)):
            try:
                previous = self._last_write

                def _ordered_write() -> None:
                    if previous is not None:
                        previous.result()
                    self._write_sync(action, callback)

                self._last_write = _LINEAGE_WRITE_EXECUTOR.submit(_ordered_write)
            except RuntimeError as exc:
                logger.warning("[ProcessingLineage] %s 异步提交失败: %s", action, exc)
            return
        self._write_sync(action, callback)

    def _write_sync(self, action: str, callback) -> None:
        try:
            callback(self._repository())
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            logger.warning("[ProcessingLineage] %s 失败，主链路继续: %s", action, exc)
        except Exception as exc:  # 数据库驱动可能抛出第三方异常，必须隔离主链路
            logger.warning("[ProcessingLineage] %s 异常，主链路继续: %s", action, exc)

    def start(self) -> None:
        snapshot = self.context.snapshot()
        self._write("创建运行", lambda repository: repository.create_run(snapshot))

    def begin_stage(self, stage: str, *, role: str | None, engine_type: str,
                    model: ModelIdentity | None = None,
                    skip_reason: str | None = None,
                    metadata: dict[str, Any] | None = None) -> tuple[str, float]:
        return (
            self.context.begin_stage(
                stage,
                role=role,
                engine_type=engine_type,
                model=model,
                skip_reason=skip_reason,
                metadata=metadata,
            ),
            time.monotonic(),
        )

    def bind_stage(self, step_id: str | None):
        if not step_id:
            return nullcontext()
        return self.context.bind_stage(step_id)

    def set_stage_model(self, step_id: str | None, model: ModelIdentity) -> None:
        if step_id:
            self.context.set_stage_model(step_id, model)

    def finish_stage(self, step_id: str | None, *, status: StageStatus,
                     started_at: float, input_count: int = 0,
                     output_count: int = 0, cache_status: str = "miss",
                     usage: dict[str, Any] | None = None,
                     retry_count: int = 0, fallback_reason: str | None = None,
                     error_message: str | None = None,
                     skip_reason: str | None = None,
                     prompt_key: str | None = None,
                     prompt_version: str | None = None,
                     prompt_hash: str | None = None,
                     taxonomy_version: str | None = None,
                     rules_version: str | None = None,
                     schema_fingerprint: str | None = None,
                     metadata: dict[str, Any] | None = None) -> None:
        if not step_id:
            return
        self.context.finish_stage(
            step_id,
            status=status,
            input_count=input_count,
            output_count=output_count,
            cache_status=cache_status,
            usage=usage,
            duration_ms=(time.monotonic() - started_at) * 1000,
            retry_count=retry_count,
            fallback_reason=fallback_reason,
            error_message=error_message,
            skip_reason=skip_reason,
            prompt_key=prompt_key,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            taxonomy_version=taxonomy_version,
            rules_version=rules_version,
            schema_fingerprint=schema_fingerprint,
            metadata=metadata,
        )
        self.persist_stage(step_id)

    def persist_stage(self, step_id: str) -> None:
        snapshot = self.context.snapshot()
        step = next((item for item in snapshot.steps if item.step_id == step_id), None)
        if step is None:
            raise KeyError(f"未知处理阶段: {step_id}")
        self._write("写入阶段", lambda repository: repository.upsert_stage(step))

    def finish(self, status: str, *, error_message: str | None = None) -> None:
        self.context.finish(status, error_message=error_message)
        snapshot = self.context.snapshot()
        self._write(
            "完成运行",
            lambda repository: repository.finish_run(
                snapshot.run_id,
                snapshot.status,
                finished_at=snapshot.finished_at,
                error_message=snapshot.error_message,
                model_summary=snapshot.model_summary,
            ),
        )
        # 主链路只在 run 结束时等待一次，确保 API 收到 done 后可以立即查询
        # 到完整阶段；不同 run 仍由共享线程池并行写入。
        if self._last_write is not None:
            try:
                self._last_write.result(timeout=10)
            except TimeoutError:
                logger.warning(
                    "[ProcessingLineage] 完成写入等待超时 run_id=%s，后续由异步队列继续",
                    snapshot.run_id,
                )
            except Exception as exc:
                logger.warning(
                    "[ProcessingLineage] 完成写入等待异常 run_id=%s: %s",
                    snapshot.run_id,
                    exc,
                )

    def fail(self, error_message: str) -> None:
        """把尚未收口的阶段标为失败，再完成运行。"""

        for step in self.context.snapshot().steps:
            if step.status == "running":
                self.context.finish_stage(
                    step.step_id,
                    status="failed",
                    error_message=error_message[:2000],
                )
                self.persist_stage(step.step_id)
        self.finish("failed", error_message=error_message[:2000])
