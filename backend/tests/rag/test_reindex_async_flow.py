"""reindex remote 断层收口（2026-10-02）的任务化链路测试。

覆盖：queue_router 登记、提交口活动任务 join、任务执行体成功/可重试失败/
终态失败/advisory 争用、执行体 run_reindex 成功与互斥路径。
只 mock 外部边界（PG 连接 / indexer / 进度镜像），不 mock 被测逻辑。
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.rag.indexing import reindex_service
from backend.rag.indexing.reindex_service import ReindexInProgressError
from backend.tasks.index_tasks import reindex_document_task_impl
from backend.tasks.queue_router import resolve_for_celery_task, resolve_for_workflow
from backend.tasks.retry_policy import TaskRetryScheduled


# ═══════════════════════════════════════════════════
# fakes（只替外部边界）
# ═══════════════════════════════════════════════════

class _FakeCursor:
    def __init__(self, acquired: bool):
        self._acquired = acquired

    def execute(self, *args, **kwargs):
        return None

    def fetchone(self):
        return [self._acquired]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeLockConn:
    def __init__(self, acquired: bool = True):
        self._cursor = _FakeCursor(acquired)

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeRegistry:
    def __init__(self, doc: dict):
        self.doc = dict(doc)

    def get_by_doc_id(self, doc_id: str):
        return dict(self.doc) if doc_id == self.doc.get("doc_id") else None


class _FakeIndexer:
    instances: list["_FakeIndexer"] = []
    reindex_result: dict = {"chunk_count": 7, "file_hash": "h1",
                            "trace_id": "tr-1", "skipped": False}

    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        _FakeIndexer.instances.append(self)

    def reindex_file(self, file_path: str) -> dict:
        return dict(self.reindex_result)


@pytest.fixture()
def fake_lock_acquired(monkeypatch):
    @contextmanager
    def _lock():
        yield _FakeLockConn(True)
    monkeypatch.setattr(reindex_service, "_lock_connection", _lock)


@pytest.fixture()
def doc_env(tmp_path, monkeypatch, fake_lock_acquired):
    """一份真实存在的源文件 + fake registry/pipeline/indexer。"""
    src = tmp_path / "财务政策.txt"
    src.write_text("2026 年度差旅报销政策：经济舱凭票报销。", encoding="utf-8")
    doc = {"doc_id": "doc-it-1", "file_name": "财务政策.txt",
           "file_path": str(src), "kb_id": "policy_general",
           "department": "finance"}
    registry = _FakeRegistry(doc)
    pipeline = SimpleNamespace(
        vectordb=object(), doc_db=object(), embedding=object(),
        bm25_store=object(),
        refresh_bm25_from_store=MagicMock())
    monkeypatch.setattr("backend.rag.indexing.indexer.IncrementalIndexer",
                        _FakeIndexer)
    _FakeIndexer.instances = []
    _FakeIndexer.reindex_result = {"chunk_count": 7, "file_hash": "h1",
                                   "trace_id": "tr-1", "skipped": False}
    return SimpleNamespace(doc=doc, registry=registry, pipeline=pipeline,
                           file_path=str(src))


# ═══════════════════════════════════════════════════
# queue_router 登记（fail-closed：未登记 = 投递即抛）
# ═══════════════════════════════════════════════════

def test_reindex_task_registered_to_rag_index_queue():
    route = resolve_for_celery_task("tasks.reindex_document")
    assert route.physical_queue == resolve_for_workflow("rag_index").physical_queue
    assert route.routing_reason == "workflow_binding"


# ═══════════════════════════════════════════════════
# 执行体 run_reindex（本地同步路径与 worker 共用）
# ═══════════════════════════════════════════════════

def test_run_reindex_success(doc_env, monkeypatch):
    logged: dict = {}
    monkeypatch.setattr(reindex_service, "log_reindex_success",
                        lambda *a, **k: logged.update(k, doc_id=a[0]))
    events: list[tuple] = []
    result = reindex_service.run_reindex(
        "doc-it-1", registry=doc_env.registry, pipeline=doc_env.pipeline,
        batch_id="b-1", source="test",
        emit=lambda stage, message="", **extra: events.append((stage, extra)))

    assert result["ok"] is True
    assert result["chunk_count"] == 7
    assert result["hash"] == "h1"
    assert result["skipped"] is False
    # indexer 收到 registry 回读的归属（防覆盖默认值口径）
    assert _FakeIndexer.instances, "IncrementalIndexer 未被构造"
    assert _FakeIndexer.instances[0].kwargs["kb_id"] == "policy_general"
    assert _FakeIndexer.instances[0].kwargs["department"] == "finance"
    assert _FakeIndexer.instances[0].kwargs["processing_task_id"] == "reindex:doc-it-1"
    assert _FakeIndexer.instances[0].kwargs["processing_batch_id"] == "b-1"
    # 发布方本进程刷新 BM25 + 终态事件
    doc_env.pipeline.refresh_bm25_from_store.assert_called_once()
    assert events and events[-1][0] == "done"
    assert events[-1][1]["chunk_count"] == 7
    # 成功操作日志（executor 标注执行场所）
    assert logged.get("doc_id") == "doc-it-1"
    assert logged.get("executor") == "rag_index_worker"


def test_run_reindex_skipped_dedup(doc_env, monkeypatch):
    _FakeIndexer.reindex_result = {"chunk_count": 0, "file_hash": "h1",
                                   "trace_id": "", "skipped": True}
    monkeypatch.setattr(reindex_service, "log_reindex_success", MagicMock())
    events: list[tuple] = []
    result = reindex_service.run_reindex(
        "doc-it-1", registry=doc_env.registry, pipeline=doc_env.pipeline,
        emit=lambda stage, message="", **extra: events.append(stage))
    assert result["skipped"] is True
    # dedup 命中：没有新数据，不得触发 BM25 刷新/清理
    doc_env.pipeline.refresh_bm25_from_store.assert_not_called()
    assert events[-1] == "duplicate"


def test_run_reindex_lock_contention(doc_env, monkeypatch):
    @contextmanager
    def _lock_busy():
        yield _FakeLockConn(False)
    monkeypatch.setattr(reindex_service, "_lock_connection", _lock_busy)
    with pytest.raises(ReindexInProgressError):
        reindex_service.run_reindex(
            "doc-it-1", registry=doc_env.registry, pipeline=doc_env.pipeline)
    assert not _FakeIndexer.instances, "争用方不得构造 indexer"


def test_run_reindex_missing_file(doc_env, monkeypatch):
    os.remove(doc_env.file_path)
    with pytest.raises(ValueError):
        reindex_service.run_reindex(
            "doc-it-1", registry=doc_env.registry, pipeline=doc_env.pipeline)


# ═══════════════════════════════════════════════════
# Celery 任务执行体（无 TaskState 行模式，db_task_id=None）
# ═══════════════════════════════════════════════════

def test_reindex_task_impl_success(doc_env, monkeypatch):
    monkeypatch.setattr(reindex_service, "run_reindex",
                        MagicMock(return_value={
                            "ok": True, "doc_id": "doc-it-1",
                            "chunk_count": 7, "hash": "h1",
                            "doc": {"doc_id": "doc-it-1"}, "skipped": False,
                            "trace_id": "tr-1"}))
    result = reindex_document_task_impl("doc-it-1", batch_id="b-1",
                                        source="test", db_task_id=None)
    assert result == {"status": "done", "doc_id": "doc-it-1",
                      "chunk_count": 7, "skipped": False, "trace_id": "tr-1"}


def test_reindex_task_impl_retryable_failure(monkeypatch):
    err = RuntimeError("embedding upstream 500")
    err.error_type = "provider_error"  # provider 包装异常口径 → 可重试
    monkeypatch.setattr(reindex_service, "run_reindex", MagicMock(side_effect=err))
    with pytest.raises(TaskRetryScheduled):
        reindex_document_task_impl("doc-it-1", db_task_id=None, retries=0)


def test_reindex_task_impl_terminal_failure(monkeypatch):
    monkeypatch.setattr(
        reindex_service, "run_reindex",
        MagicMock(side_effect=ValueError("文件不存在: /gone.txt")))
    failed: dict = {}
    monkeypatch.setattr(reindex_service, "log_reindex_failed",
                        lambda *a, **k: failed.update({"args": a, **k}))
    events: list[str] = []

    def _emit(stage, message="", **extra):
        events.append(stage)
    monkeypatch.setattr("backend.tasks.index_tasks._reindex_redis_emit_fn",
                        lambda doc_id: _emit)
    with pytest.raises(ValueError):
        reindex_document_task_impl("doc-it-1", db_task_id=None,
                                   source="test", retries=0)
    # 终态：失败操作日志已写 + error 终态事件（validation_error 不进重试）
    assert any("文件不存在" in str(v) for v in failed.values())
    assert events and events[-1] == "error"


def test_reindex_task_impl_lock_busy_is_terminal(doc_env, monkeypatch):
    """advisory 争用 → validation_error 终态（不进重试预算）。"""
    @contextmanager
    def _lock_busy():
        yield _FakeLockConn(False)
    monkeypatch.setattr(reindex_service, "_lock_connection", _lock_busy)
    failed: dict = {}
    monkeypatch.setattr(reindex_service, "log_reindex_failed",
                        lambda *a, **k: failed.update(kwargs=k))
    events: list[str] = []

    def _emit(stage, message="", **extra):
        events.append(stage)
    monkeypatch.setattr("backend.tasks.index_tasks._reindex_redis_emit_fn",
                        lambda doc_id: _emit)
    with pytest.raises(ValueError):
        reindex_document_task_impl("doc-it-1", db_task_id=None,
                                   source="test", retries=0)
    assert failed, "终态失败必须写失败操作日志"
    assert events and events[-1] == "error"


# ═══════════════════════════════════════════════════
# 提交口（活动任务 join）
# ═══════════════════════════════════════════════════

def _fake_task_record(status):
    from backend.models.task import TaskStatus
    from backend.models.task import TaskRecord

    return TaskRecord(id="t-1", user_id="u1", status=status,
                      biz_type="rag_reindex", biz_id="doc-it-1")


def test_submit_joins_active_task(monkeypatch):
    from backend.app.api.routes import rag_documents
    from backend.models.task import TaskStatus
    from backend.services import task_service

    monkeypatch.setattr(task_service, "get_latest_biz_task",
                        lambda biz_type, biz_id: _fake_task_record(TaskStatus.RUNNING))
    # join 路径不得触达入队：index_tasks 导入即注册，这里 monkeypatch
    # create_record 兜底防误建
    monkeypatch.setattr(
        "backend.tasks.index_task_runtime.create_reindex_task_record",
        MagicMock(side_effect=AssertionError("join 路径不得创建任务行")))
    resp = rag_documents._submit_reindex_task(
        "doc-it-1", "财务政策.txt", batch_id=None, source="test",
        user_id="u1", tenant_id="default")
    assert resp["ok"] is True and resp["joined"] is True
    assert resp["task_id"] == "t-1" and resp["status"] == "RUNNING"


def test_submit_creates_and_enqueues(monkeypatch):
    from backend.app.api.routes import rag_documents
    from backend.services import task_service

    monkeypatch.setattr(task_service, "get_latest_biz_task",
                        lambda biz_type, biz_id: None)
    monkeypatch.setattr(
        "backend.tasks.index_task_runtime.create_reindex_task_record",
        lambda doc_id, name, **kw: "t-new")
    queued: dict = {}
    monkeypatch.setattr(task_service, "mark_queued",
                        lambda task_id, celery_id, **kw: queued.update(
                            {"task_id": task_id, "celery_id": celery_id}))

    class _FakeAsyncResult:
        id = "celery-1"

    class _FakeCeleryTask:
        def apply_async(self, *, kwargs, queue):
            assert queue == resolve_for_workflow("rag_index").physical_queue
            assert kwargs["doc_id"] == "doc-it-1"
            assert kwargs["db_task_id"] == "t-new"
            return _FakeAsyncResult()

    import backend.tasks.index_tasks as index_tasks
    monkeypatch.setattr(index_tasks, "reindex_document_task", _FakeCeleryTask())

    resp = rag_documents._submit_reindex_task(
        "doc-it-1", "财务政策.txt", batch_id="b-9", source="test",
        user_id="u1", tenant_id="default")
    assert resp["ok"] is True and resp["joined"] is False
    assert resp["task_id"] == "t-new" and resp["async"] is True
    assert queued == {"task_id": "t-new", "celery_id": "celery-1"}
