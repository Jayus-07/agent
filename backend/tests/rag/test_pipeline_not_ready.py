"""TD-16 RAGPipeline 未就绪自愈回归（2026-10-03）。

实测背景：Docker 引擎崩溃恢复时 postgres 与 rag-service/worker 并发
重启（restart policy 不吃 depends_on condition），rag-service 初始化
探到 PG "starting up" → _pipeline_init_error 永久驻留（"重试中"文案
无实际重试）→ readyz 503 直到手动重启；索引任务的 RuntimeError 被
分类器误归 validation_error 一次终败。

钉死：
  1. PipelineNotReadyError 分类为 service_unavailable/retryable；
  2. init 失败退避窗口内快速失败；
  3. 窗口过期后允许重新初始化（请求驱动自愈）。
"""
import threading

import pytest

from backend.tasks.error_taxonomy import classify_task_error
from backend.rag import pipeline as pl


@pytest.fixture(autouse=True)
def _reset_pipeline_state():
    pl._pipeline_singleton = None
    pl._pipeline_init_error = None
    pl._pipeline_last_init_failure_at = 0.0
    yield
    pl._pipeline_singleton = None
    pl._pipeline_init_error = None
    pl._pipeline_last_init_failure_at = 0.0


def test_not_ready_error_is_retryable():
    exc = pl.PipelineNotReadyError("RAG 服务不可用（重试中）: x")
    decision = classify_task_error(exc)
    assert decision.error_type == "service_unavailable"
    assert decision.retryable is True
    assert decision.source == "runtime"


def test_not_ready_is_runtime_error_compat():
    """现有 except RuntimeError 调用方（rag_server 503 等）不受影响。"""
    assert issubclass(pl.PipelineNotReadyError, RuntimeError)


def test_backoff_window_fast_fail(monkeypatch):
    """init 失败后退避窗口内：不重复 init，直接快速失败。"""
    calls = []
    monkeypatch.setattr(
        pl, "RAGPipeline",
        lambda mode="runtime": (_ for _ in ()).throw(RuntimeError("pg starting")),
    )
    with pytest.raises(pl.PipelineNotReadyError):
        pl._get_local_pipeline()
    assert pl._pipeline_init_error is not None
    first_fail_at = pl._pipeline_last_init_failure_at
    # 窗口内再次调用：不应重试 init（calls 不会增加——直接走 raise 分支）
    monkeypatch.setattr(
        pl, "RAGPipeline",
        lambda mode="runtime": calls.append(1) or object(),
    )
    with pytest.raises(pl.PipelineNotReadyError):
        pl._get_local_pipeline()
    assert calls == []  # 窗口内未重试
    assert pl._pipeline_last_init_failure_at == first_fail_at


def test_backoff_expiry_retries_init(monkeypatch):
    """窗口过期 → 重新尝试 init；成功则单例就绪。"""
    import backend.rag.pipeline as pl_mod
    monkeypatch.setattr(
        pl, "RAGPipeline",
        lambda mode="runtime": (_ for _ in ()).throw(RuntimeError("pg starting")),
    )
    with pytest.raises(pl.PipelineNotReadyError):
        pl._get_local_pipeline()
    # 模拟时间流逝
    pl._pipeline_last_init_failure_at -= (
        pl._PIPELINE_INIT_RETRY_BACKOFF_SECONDS + 1
    )
    sentinel = object()
    monkeypatch.setattr(pl, "RAGPipeline", lambda mode="runtime": sentinel)
    got = pl._get_local_pipeline()
    assert got is sentinel
    assert pl._pipeline_init_error is None
