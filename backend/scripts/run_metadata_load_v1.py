"""运行元数据处理血缘的 ``metadata-load-v1`` 开发压测。

默认使用“缓存命中”工作负载：它会真实写入当前 PostgreSQL 的运行/阶段
血缘，并真实请求血缘详情 API，但不会调用 OCR、Embedding 或 LLM。这样在
开发阶段可以验证高并发写入、连接池、API 查询和 token 节省，不会因为压测
意外产生云模型费用。

示例（宿主机连接 Docker 暴露的 PG/API）：

    $env:PGHOST = "127.0.0.1"
    $env:PGPORT = "5433"
    D:/Python/python.exe backend/scripts/run_metadata_load_v1.py `
      --peak-concurrency 4 --requests 64 `
      --api-base-url http://127.0.0.1:8000 `
      --output docs/evidence/metadata/metadata-load-v1-20260920.json

报告只提供事实证据；是否允许发布由
``backend.eval.metadata_baseline.validate_release`` 的 fail-closed 门禁决定。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

# 允许从仓库根目录直接执行 `python backend/scripts/...`。
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from backend.eval.metadata_baseline.load_report import build_load_report
from backend.rag.indexing.processing_lineage import ModelIdentity, ProcessingRunContext
from backend.rag.indexing.processing_lineage_pg import (
    get_processing_lineage_repository,
)
from backend.rag.indexing.processing_lineage_recorder import ProcessingRunRecorder


@dataclass(frozen=True)
class _TaskObservation:
    doc_id: str
    run_id: str
    queue_wait_ms: float
    processing_ms: float
    error: str = ""


class _ActiveTracker:
    """记录压测执行体实际达到的并发峰值。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = 0
        self.peak = 0

    def enter(self) -> None:
        with self._lock:
            self._active += 1
            self.peak = max(self.peak, self._active)

    def leave(self) -> None:
        with self._lock:
            self._active = max(self._active - 1, 0)


def _cached_model(role: str, model_name: str) -> ModelIdentity:
    return ModelIdentity(
        role=role,
        engine_type="cached_model",
        provider="metadata-load-cache",
        model_name=model_name,
        model_revision="load-fixture-v1",
        config_source="load_harness",
        config_revision="metadata-load-v1",
        artifact_fingerprint=f"metadata-load-v1:{role}:{model_name}",
    )


def _finish_stage(
    recorder: ProcessingRunRecorder,
    step_id: str,
    started_at: float,
    *,
    status: str = "success",
    cache_status: str = "miss",
    model: ModelIdentity | None = None,
    skip_reason: str | None = None,
) -> None:
    if model is not None:
        recorder.set_stage_model(step_id, model)
    recorder.finish_stage(
        step_id,
        status=status,  # type: ignore[arg-type]
        started_at=started_at,
        input_count=1,
        output_count=1,
        cache_status=cache_status,
        skip_reason=skip_reason,
    )


def _run_one(
    index: int,
    *,
    submitted_at: float,
    repository: Any,
    batch_id: str,
    active: _ActiveTracker,
    start_barrier: threading.Barrier,
    processing_delay_ms: float,
) -> _TaskObservation:
    active.enter()
    started_at = time.monotonic()
    queue_wait_ms = max((started_at - submitted_at) * 1000, 0.0)
    doc_id = f"metadata-load-v1-doc-{batch_id}-{index}"
    context = ProcessingRunContext.create(
        doc_id,
        file_hash=f"metadata-load-v1-hash-{batch_id}-{index}",
        operation="load_test",
        batch_id=batch_id,
        task_id=f"metadata-load-v1-task-{index}",
        config_snapshot={
            "profile": "cache_hit_zero_external_calls",
            "taxonomy_version": "taxonomy-v1",
            "rules_version": "rules-v1",
            "model_version": "load-fixture-v1",
            "prompt_version": "prompt-v1",
        },
    )
    recorder = ProcessingRunRecorder(context, repository)
    try:
        # 让目标 worker 尽量同时进入，避免任务太快导致实际峰值低于目标。
        try:
            start_barrier.wait(timeout=15)
        except (threading.BrokenBarrierError, TimeoutError):
            # 某个任务异常时允许其余任务继续，最终报告会记录失败数。
            pass

        recorder.start()
        stages: list[tuple[str, str, ModelIdentity | None, str, str | None]] = [
            ("load", "file_io", None, "success", None),
            ("parser", "parser", None, "success", None),
            ("ocr", "ocr", None, "skipped", "text_layer_sufficient"),
            ("semantic_chunk", "chunker", None, "success", None),
            (
                "metadata_extract",
                "cached_model",
                _cached_model("metadata", "qwen3.7-plus"),
                "cached",
                None,
            ),
            (
                "embedding",
                "cached_model",
                _cached_model("embedding", "qwen3.7-text-embedding"),
                "cached",
                None,
            ),
            ("vector_write", "vector_store", None, "success", None),
        ]
        for stage, engine_type, model, status, skip_reason in stages:
            step_id, stage_started = recorder.begin_stage(
                stage,
                role=model.role if model else None,
                engine_type=engine_type,
                model=model,
                skip_reason=skip_reason,
            )
            if status == "skipped":
                # skipped 阶段已经在 begin_stage 中收口；不再重复完成。
                recorder.persist_stage(step_id)
                continue
            _finish_stage(
                recorder,
                step_id,
                stage_started,
                status=status,
                cache_status="hit" if status == "cached" else "miss",
                model=model,
            )

        if processing_delay_ms > 0:
            time.sleep(processing_delay_ms / 1000)
        recorder.finish("success")
        return _TaskObservation(
            doc_id=doc_id,
            run_id=context.run_id,
            queue_wait_ms=queue_wait_ms,
            processing_ms=(time.monotonic() - submitted_at) * 1000,
        )
    except Exception as exc:
        lineage_error = ""
        try:
            recorder.fail(str(exc))
        except Exception as finalize_exc:
            # 压测失败时保留原始失败原因；同时把血缘收口异常作为附加诊断。
            lineage_error = f"; lineage_finalize={type(finalize_exc).__name__}: {finalize_exc}"
        return _TaskObservation(
            doc_id=doc_id,
            run_id=context.run_id,
            queue_wait_ms=queue_wait_ms,
            processing_ms=(time.monotonic() - submitted_at) * 1000,
            error=f"{type(exc).__name__}: {exc}{lineage_error}",
        )
    finally:
        active.leave()


def _request_lineage_detail(
    api_base_url: str,
    observation: _TaskObservation,
    *,
    headers: dict[str, str],
    timeout_seconds: float,
) -> tuple[float, str]:
    """请求一次血缘详情，返回耗时和错误原因。"""

    path = (
        f"{api_base_url.rstrip('/')}/rag/documents/"
        f"{quote(observation.doc_id, safe='')}/processing-runs/"
        f"{quote(observation.run_id, safe='')}"
    )
    request = Request(path, headers=headers, method="GET")
    started = time.monotonic()
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
            status_code = int(response.status)
        elapsed_ms = (time.monotonic() - started) * 1000
        if status_code != 200:
            return elapsed_ms, f"http_status_{status_code}"
        if not isinstance(payload, dict) or not payload.get("steps"):
            return elapsed_ms, "lineage_steps_missing"
        return elapsed_ms, ""
    except HTTPError as exc:
        return (time.monotonic() - started) * 1000, f"http_status_{exc.code}"
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        return (time.monotonic() - started) * 1000, f"{type(exc).__name__}: {exc}"


def _duplicate_count(repository: Any, run_ids: list[str]) -> int:
    counter = getattr(repository, "count_duplicate_stage_keys", None)
    if not callable(counter):
        raise RuntimeError("当前血缘仓储未提供重复阶段键统计能力")
    return int(counter(run_ids))


def run_load(args: argparse.Namespace) -> dict[str, Any]:
    baseline = max(int(args.peak_concurrency), 1)
    target = baseline * 2
    request_count = int(args.requests or max(target * 8, 32))
    if request_count < target:
        raise ValueError("--requests 必须不小于 2 倍峰值并发")
    batch_id = args.batch_id or f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    repository = get_processing_lineage_repository()
    active = _ActiveTracker()
    barrier = threading.Barrier(target)
    observations: list[_TaskObservation] = []
    load_started = time.monotonic()

    with ThreadPoolExecutor(max_workers=target, thread_name_prefix="metadata-load") as executor:
        futures = [
            executor.submit(
                _run_one,
                index,
                submitted_at=time.monotonic(),
                repository=repository,
                batch_id=batch_id,
                active=active,
                start_barrier=barrier,
                processing_delay_ms=float(args.processing_delay_ms),
            )
            for index in range(request_count)
        ]
        for future in as_completed(futures):
            observations.append(future.result())
    load_duration = max(time.monotonic() - load_started, 0.0)

    successful = [item for item in observations if not item.error]
    failed = [item for item in observations if item.error]
    run_ids = [item.run_id for item in successful]
    duplicate_count = _duplicate_count(repository, run_ids)
    pool_stats = repository.pool_wait_stats()

    api_latencies: list[float] = []
    api_errors: list[str] = []
    api_headers = {"X-User-Id": "metadata-load-v1", "X-User-Roles": "admin"}
    api_key = os.getenv("API_KEY", "").strip()
    if api_key:
        api_headers["X-API-Key"] = api_key
    if args.skip_api:
        api_errors.append("api_probe_skipped")
    else:
        with ThreadPoolExecutor(
            max_workers=min(target, max(len(successful), 1)),
            thread_name_prefix="metadata-lineage-api-load",
        ) as api_executor:
            api_futures = [
                api_executor.submit(
                    _request_lineage_detail,
                    args.api_base_url,
                    observation,
                    headers=api_headers,
                    timeout_seconds=float(args.api_timeout_seconds),
                )
                for observation in successful
            ]
            for future in as_completed(api_futures):
                elapsed_ms, error = future.result()
                api_latencies.append(elapsed_ms)
                if error:
                    api_errors.append(error)

    report = build_load_report(
        baseline_peak_concurrency=baseline,
        observed_peak_concurrency=active.peak,
        queue_wait_ms=[item.queue_wait_ms for item in observations],
        processing_ms=[item.processing_ms for item in observations],
        lineage_api_ms=api_latencies,
        db_pool_wait_ms=[float(pool_stats.get("p95_ms", 0.0))],
        duration_seconds=load_duration,
        submitted_count=request_count,
        completed_count=len(successful),
        failed_count=len(failed),
        embedding_calls=0,
        llm_calls=0,
        llm_429_count=0,
        cache_hits=len(successful) * 2,
        cache_misses=0,
        duplicate_write_count=duplicate_count,
        queue_drained=True,
        shadow_enabled=False,
        api_error_count=len(api_errors),
    )
    report.update({
        "batch_id": batch_id,
        "profile": "cache_hit_zero_external_calls",
        "api_base_url": args.api_base_url,
        "api_probe_skipped": bool(args.skip_api),
        "api_error_samples": api_errors[:10],
        "task_error_samples": [item.error for item in failed[:10]],
        "run_id_samples": run_ids[:5],
        "pool_wait_max_ms": pool_stats.get("max_ms", 0.0),
        "pool_wait_sample_count": pool_stats.get(
            "sample_count", report["db_pool_wait_sample_count"]
        ),
        "generated_at_epoch": time.time(),
    })
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="metadata-load-v1 开发压测")
    parser.add_argument(
        "--peak-concurrency",
        type=int,
        default=int(os.getenv("CELERY_WORKER_CONCURRENCY", "4")),
        help="当前生产/Compose 预估峰值并发；压测自动运行其 2 倍",
    )
    parser.add_argument("--requests", type=int, default=0)
    parser.add_argument("--processing-delay-ms", type=float, default=20.0)
    parser.add_argument(
        "--api-base-url",
        default=os.getenv("METADATA_LOAD_API_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument("--api-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--skip-api", action="store_true")
    parser.add_argument("--batch-id", default="")
    parser.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_load(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not report["failed_count"] and not report["api_error_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
