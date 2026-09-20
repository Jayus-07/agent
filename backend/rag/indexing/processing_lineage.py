"""RAG 文档处理运行与阶段的领域契约。

该模块只负责内存中的状态、版本快照和校验；数据库持久化由
``processing_lineage_pg.py`` 完成，避免索引主链路依赖具体数据库实现。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Literal, Mapping

from backend.shared.processing_context import ProcessingBinding, bind_processing


StageStatus = Literal["running", "success", "skipped", "cached", "fallback", "failed"]
RunStatus = Literal["running", "success", "failed", "duplicate", "cancelled"]

_TERMINAL_STAGE_STATUSES = frozenset({
    "success", "skipped", "cached", "fallback", "failed",
})
_TERMINAL_RUN_STATUSES = frozenset({
    "success", "failed", "duplicate", "cancelled",
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _version_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_usage(usage: Mapping[str, Any] | None) -> dict[str, Any]:
    if not usage:
        return {}

    result = dict(usage)
    token_fields = (
        "prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens",
        "reasoning_tokens",
    )
    for field_name in token_fields:
        if field_name not in result or result[field_name] is None:
            continue
        value = int(result[field_name])
        if value < 0:
            raise ValueError("Token 不能为负数")
        result[field_name] = value

    prompt = int(result.get("prompt_tokens") or 0)
    completion = int(result.get("completion_tokens") or 0)
    if "total_tokens" not in result and (prompt or completion):
        result["total_tokens"] = prompt + completion
    if result.get("cost_usd") is not None:
        cost = float(result["cost_usd"])
        if cost < 0:
            raise ValueError("cost_usd 不能为负数")
        result["cost_usd"] = cost
    return result


@dataclass(frozen=True)
class ModelIdentity:
    """一次实际模型/引擎执行的身份快照。"""

    role: str | None
    engine_type: str
    provider: str | None = None
    model_name: str | None = None
    model_revision: str | None = None
    config_source: str | None = None
    config_revision: str | None = None
    artifact_fingerprint: str | None = None


@dataclass
class ProcessingStepSnapshot:
    step_id: str
    run_id: str
    stage: str
    ordinal: int
    attempt_no: int
    status: StageStatus
    role: str | None = None
    engine_type: str = ""
    provider: str | None = None
    model_name: str | None = None
    model_revision: str | None = None
    config_source: str | None = None
    config_revision: str | None = None
    artifact_fingerprint: str | None = None
    prompt_key: str | None = None
    prompt_version: str | None = None
    prompt_hash: str | None = None
    taxonomy_version: str | None = None
    rules_version: str | None = None
    schema_fingerprint: str | None = None
    cache_status: str = "miss"
    input_count: int = 0
    output_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: float = 0.0
    retry_count: int = 0
    fallback_reason: str | None = None
    skip_reason: str | None = None
    error_message: str | None = None
    started_at: datetime = field(default_factory=_now)
    finished_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessingRunSnapshot:
    run_id: str
    doc_id: str
    file_hash: str
    operation: str
    status: RunStatus
    pipeline_version: str
    git_sha: str | None
    config_snapshot_hash: str
    config_snapshot: dict[str, Any]
    task_id: str | None
    batch_id: str | None
    trace_id: str | None
    started_at: datetime
    finished_at: datetime | None
    error_message: str | None
    model_summary: list[dict[str, Any]]
    steps: list[ProcessingStepSnapshot]


class ProcessingRunContext:
    """一个文件处理运行的内存状态机。"""

    def __init__(
        self,
        *,
        run_id: str,
        doc_id: str,
        file_hash: str,
        operation: str,
        pipeline_version: str,
        git_sha: str | None,
        config_snapshot: Mapping[str, Any],
        task_id: str | None,
        batch_id: str | None,
        trace_id: str | None,
    ) -> None:
        self.run_id = run_id
        self.doc_id = doc_id
        self.file_hash = file_hash
        self.operation = operation
        self.pipeline_version = pipeline_version
        self.git_sha = git_sha
        self.config_snapshot = dict(config_snapshot)
        self.config_snapshot_hash = _version_hash(self.config_snapshot)
        self.task_id = task_id
        self.batch_id = batch_id
        self.trace_id = trace_id
        self.started_at = _now()
        self.finished_at: datetime | None = None
        self.status: RunStatus = "running"
        self.error_message: str | None = None
        self._steps: dict[str, ProcessingStepSnapshot] = {}
        self._next_ordinal = 0

    @classmethod
    def create(
        cls,
        doc_id: str,
        file_hash: str,
        operation: str,
        *,
        task_id: str | None = None,
        batch_id: str | None = None,
        trace_id: str | None = None,
        pipeline_version: str = "rag-processing-v1",
        git_sha: str | None = None,
        config_snapshot: Mapping[str, Any] | None = None,
    ) -> "ProcessingRunContext":
        return cls(
            run_id=str(uuid.uuid4()),
            doc_id=doc_id,
            file_hash=file_hash,
            operation=operation,
            pipeline_version=pipeline_version,
            git_sha=git_sha,
            config_snapshot=config_snapshot or {},
            task_id=task_id,
            batch_id=batch_id,
            trace_id=trace_id,
        )

    def begin_stage(
        self,
        stage: str,
        *,
        role: str | None,
        engine_type: str,
        ordinal: int | None = None,
        attempt_no: int = 1,
        model: ModelIdentity | None = None,
        skip_reason: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        if self.status in _TERMINAL_RUN_STATUSES:
            raise RuntimeError("运行已完成，不能新增阶段")
        if skip_reason and model is not None:
            raise ValueError("skipped 阶段不能填写实际模型")
        if not stage.strip():
            raise ValueError("stage 不能为空")
        if attempt_no < 1:
            raise ValueError("attempt_no 必须从 1 开始")

        step_id = str(uuid.uuid4())
        effective_ordinal = self._next_ordinal if ordinal is None else int(ordinal)
        self._next_ordinal = max(self._next_ordinal, effective_ordinal + 1)
        step_status: StageStatus = "skipped" if skip_reason else "running"
        step = ProcessingStepSnapshot(
            step_id=step_id,
            run_id=self.run_id,
            stage=stage,
            ordinal=effective_ordinal,
            attempt_no=attempt_no,
            status=step_status,
            role=role,
            engine_type=engine_type,
            provider=model.provider if model else None,
            model_name=model.model_name if model else None,
            model_revision=model.model_revision if model else None,
            config_source=model.config_source if model else None,
            config_revision=model.config_revision if model else None,
            artifact_fingerprint=model.artifact_fingerprint if model else None,
            skip_reason=skip_reason,
            finished_at=_now() if skip_reason else None,
            metadata=dict(metadata or {}),
        )
        self._steps[step_id] = step
        return step_id

    def finish_stage(
        self,
        step_id: str,
        *,
        status: StageStatus,
        input_count: int = 0,
        output_count: int = 0,
        cache_status: str = "miss",
        usage: Mapping[str, Any] | None = None,
        duration_ms: float = 0.0,
        retry_count: int = 0,
        fallback_reason: str | None = None,
        error_message: str | None = None,
        skip_reason: str | None = None,
        prompt_key: str | None = None,
        prompt_version: str | None = None,
        prompt_hash: str | None = None,
        taxonomy_version: str | None = None,
        rules_version: str | None = None,
        schema_fingerprint: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        step = self._steps.get(step_id)
        if step is None:
            raise KeyError(f"未知处理阶段: {step_id}")
        if step.status in _TERMINAL_STAGE_STATUSES:
            raise RuntimeError("阶段已完成，不能重复完成")
        if status not in _TERMINAL_STAGE_STATUSES:
            raise ValueError("finish_stage 必须使用终态")
        if status == "skipped" and any((
            step.provider,
            step.model_name,
            step.model_revision,
            step.artifact_fingerprint,
        )):
            raise ValueError("skipped 阶段不能填写实际模型")
        if input_count < 0 or output_count < 0 or retry_count < 0:
            raise ValueError("阶段计数不能为负数")

        normalized_usage = _validate_usage(usage)
        step.status = status
        step.input_count = int(input_count)
        step.output_count = int(output_count)
        step.cache_status = cache_status
        step.prompt_tokens = int(normalized_usage.get("prompt_tokens") or 0)
        step.completion_tokens = int(normalized_usage.get("completion_tokens") or 0)
        step.total_tokens = int(normalized_usage.get("total_tokens") or 0)
        step.cached_tokens = int(normalized_usage.get("cached_tokens") or 0)
        step.reasoning_tokens = int(normalized_usage.get("reasoning_tokens") or 0)
        step.cost_usd = float(normalized_usage.get("cost_usd") or 0.0)
        step.duration_ms = max(float(duration_ms or 0.0), 0.0)
        step.retry_count = int(retry_count)
        step.fallback_reason = fallback_reason
        step.error_message = error_message
        step.skip_reason = skip_reason
        step.prompt_key = prompt_key
        step.prompt_version = prompt_version
        step.prompt_hash = prompt_hash
        step.taxonomy_version = taxonomy_version
        step.rules_version = rules_version
        step.schema_fingerprint = schema_fingerprint
        if metadata:
            step.metadata.update(dict(metadata))
        step.finished_at = _now()

    def set_stage_model(self, step_id: str, model: ModelIdentity) -> None:
        """在路由结果确定后补写实际模型身份。

        OCR、元数据级联这类阶段开始时无法知道是否真的会调用模型，
        因此先以无模型状态创建阶段，命中模型分支后再补齐身份。
        """
        step = self._steps.get(step_id)
        if step is None:
            raise KeyError(f"未知处理阶段: {step_id}")
        if step.status in _TERMINAL_STAGE_STATUSES:
            raise RuntimeError("阶段已完成，不能修改模型身份")
        if not model.engine_type.strip():
            raise ValueError("模型 engine_type 不能为空")
        step.role = model.role or step.role
        step.engine_type = model.engine_type
        step.provider = model.provider
        step.model_name = model.model_name
        step.model_revision = model.model_revision
        step.config_source = model.config_source
        step.config_revision = model.config_revision
        step.artifact_fingerprint = model.artifact_fingerprint

    @contextmanager
    def bind_stage(self, step_id: str) -> Iterator[ProcessingStepSnapshot]:
        step = self._steps.get(step_id)
        if step is None:
            raise KeyError(f"未知处理阶段: {step_id}")
        binding = ProcessingBinding(
            run_id=self.run_id,
            step_id=step.step_id,
            role=step.role,
            stage=step.stage,
        )
        with bind_processing(binding):
            yield step

    def finish(self, status: RunStatus, *, error_message: str | None = None) -> None:
        if self.status in _TERMINAL_RUN_STATUSES:
            raise RuntimeError("运行已完成，不能重复完成")
        if status not in _TERMINAL_RUN_STATUSES:
            raise ValueError("finish 必须使用终态")
        unfinished = [step.stage for step in self._steps.values() if step.status == "running"]
        if unfinished:
            raise RuntimeError(f"仍有未完成阶段: {','.join(unfinished)}")
        self.status = status
        self.error_message = error_message
        self.finished_at = _now()

    def model_summary(self) -> list[dict[str, Any]]:
        seen: set[tuple[str, str, str]] = set()
        result: list[dict[str, Any]] = []
        for step in sorted(self._steps.values(), key=lambda item: item.ordinal):
            if not step.model_name:
                continue
            key = (step.role or "", step.provider or "", step.model_name)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "stage": step.stage,
                "role": step.role,
                "provider": step.provider,
                "model_name": step.model_name,
                "model_revision": step.model_revision,
            })
        return result

    def snapshot(self) -> ProcessingRunSnapshot:
        return ProcessingRunSnapshot(
            run_id=self.run_id,
            doc_id=self.doc_id,
            file_hash=self.file_hash,
            operation=self.operation,
            status=self.status,
            pipeline_version=self.pipeline_version,
            git_sha=self.git_sha,
            config_snapshot_hash=self.config_snapshot_hash,
            config_snapshot=dict(self.config_snapshot),
            task_id=self.task_id,
            batch_id=self.batch_id,
            trace_id=self.trace_id,
            started_at=self.started_at,
            finished_at=self.finished_at,
            error_message=self.error_message,
            model_summary=self.model_summary(),
            steps=list(self._steps.values()),
        )
