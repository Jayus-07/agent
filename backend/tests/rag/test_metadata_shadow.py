"""元数据影子评估的非阻塞与故障隔离测试。"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.rag.indexing.stages.metadata_stage import MetadataStage
from backend.rag.preprocessing import metadata_shadow


TEXT = "本合同由甲方与乙方签订。第一条：服务范围。"
META = {"source_file": "unknown.docx", "file_path": "", "doc_id": "shadow-test-1"}


class _EmptyRegistry:
    def list_all(self):
        return {}


class _StageEmbedding:
    model_name = "metadata-shadow-test"


@pytest.fixture
def stage():
    return MetadataStage(_EmptyRegistry(), _StageEmbedding(), department="test")


@pytest.mark.asyncio
async def test_main_metadata_result_returns_without_waiting_for_shadow(monkeypatch, stage):
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_ENABLED", False)
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_SHADOW_ENABLED", False)

    async def _fake_extract(*args, **kwargs):
        return {
            "doc_type": "legal",
            "confidence": 0.9,
            "business_domain": "general",
            "summary": "s",
            "keywords": [],
            "entities": {},
            "time_refs": [],
        }

    def slow_submit(*args, **kwargs):
        time.sleep(1)

    monkeypatch.setattr(
        "backend.rag.preprocessing.metadata_llm.extract_metadata_llm_async",
        _fake_extract,
    )

    # 预热首次导入的中文分词词典，断言只比较影子投递是否阻塞主路径。
    await stage.build(TEXT, META)
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_SHADOW_ENABLED", True)
    monkeypatch.setattr(metadata_shadow, "submit_shadow_job", slow_submit)

    t0 = time.monotonic()
    result = await stage.build(TEXT, META)
    elapsed = time.monotonic() - t0

    assert result["doc_type"] == "legal"
    # 主路径自身包含词典/线程任务，500ms 在低配或并行回归环境下会抖动；
    # 800ms 仍显著小于 slow_submit 的 1s，足以证明没有同步等待影子投递。
    assert elapsed < 0.8


@pytest.mark.asyncio
async def test_shadow_dispatch_does_not_share_default_executor(monkeypatch, stage):
    """默认线程池拥塞时，影子提交不能占住主路径的阻塞任务槽位。"""
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_ENABLED", False)
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_SHADOW_ENABLED", False)

    async def _fake_extract(*args, **kwargs):
        return {
            "doc_type": "legal",
            "confidence": 0.9,
            "business_domain": "general",
            "summary": "s",
            "keywords": [],
            "entities": {},
            "time_refs": [],
        }

    def slow_submit(*args, **kwargs):
        time.sleep(1)

    monkeypatch.setattr(
        "backend.rag.preprocessing.metadata_llm.extract_metadata_llm_async",
        _fake_extract,
    )
    await stage.build(TEXT, META)

    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(executor)
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_SHADOW_ENABLED", True)
    monkeypatch.setattr(metadata_shadow, "submit_shadow_job", slow_submit)

    try:
        t0 = time.monotonic()
        result = await stage.build(TEXT, META)
        elapsed = time.monotonic() - t0
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    assert result["doc_type"] == "legal"
    assert elapsed < 0.8


@pytest.mark.asyncio
async def test_shadow_submission_uses_dedicated_executor(monkeypatch, stage):
    """影子投递必须使用独立线程池，不能污染主路径默认线程池。"""
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_SHADOW_ENABLED", True)
    executor = ThreadPoolExecutor(max_workers=1)
    seen = []
    monkeypatch.setattr(
        metadata_shadow,
        "get_shadow_executor",
        lambda: (seen.append(True) or executor),
    )
    monkeypatch.setattr(
        metadata_shadow,
        "submit_shadow_job",
        lambda *args, **kwargs: "shadow-test",
    )

    try:
        stage._dispatch_shadow_nonblocking(None, TEXT, "unknown.docx", "")
        await asyncio.gather(*stage._shadow_tasks)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    assert seen == [True]


@pytest.mark.asyncio
async def test_shadow_dispatch_failure_does_not_fail_primary_index(monkeypatch, stage):
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_ENABLED", False)
    monkeypatch.setattr("backend.config.rag.METADATA_CASCADE_SHADOW_ENABLED", True)

    async def _fake_extract(*args, **kwargs):
        return {
            "doc_type": "legal",
            "confidence": 0.9,
            "business_domain": "general",
            "summary": "s",
            "keywords": [],
            "entities": {},
            "time_refs": [],
        }

    def _boom(*args, **kwargs):
        raise RuntimeError("broker down")

    monkeypatch.setattr(
        "backend.rag.preprocessing.metadata_llm.extract_metadata_llm_async",
        _fake_extract,
    )
    monkeypatch.setattr(metadata_shadow, "submit_shadow_job", _boom)

    result = await stage.build(TEXT, META)

    assert result["doc_type"] == "legal"


def test_shadow_input_is_bounded_and_sampled(monkeypatch):
    captured = {}

    class _Dispatcher:
        def submit(self, job_id, payload):
            captured["job_id"] = job_id
            captured["payload"] = payload
            return True

    monkeypatch.setattr(metadata_shadow, "_shadow_dispatcher", _Dispatcher())
    monkeypatch.setattr(metadata_shadow, "_write_shadow_job", lambda **kwargs: "job-1")
    monkeypatch.setattr(metadata_shadow, "_enqueue_shadow_task", lambda job_id: True)
    monkeypatch.setattr(
        "backend.config.rag.METADATA_LLM_EXTRACT_MAX_CHARS", 20
    )

    from backend.rag.preprocessing.metadata_schema import DecisionEnvelope

    envelope = DecisionEnvelope(
        decision="accepted", doc_type="legal", source="r0", confidence=0.99
    )
    job_id = metadata_shadow.submit_shadow_job(
        envelope, "头部文本" + "x" * 100 + "尾部文本", "a.docx", "/tmp/a.docx"
    )

    assert job_id == "job-1"
    assert len(captured["payload"]["text"]) <= 20
    assert captured["payload"]["text"]


def test_shadow_worker_only_receives_job_id_and_never_calls_llm(monkeypatch):
    from datetime import datetime, timezone

    from backend.rag.preprocessing import metadata_shadow
    from backend.rag.preprocessing.metadata_router import CascadeDecision
    from backend.tasks import metadata_shadow_tasks

    job = {
        "status": "pending",
        "attempts": 0,
        "input_cache_key": "input-1",
        "main_envelope_json": {"doc_type": "legal"},
        "created_at": datetime.now(timezone.utc),
    }
    updates = []

    monkeypatch.setattr(metadata_shadow, "_load_shadow_job", lambda job_id: job)
    monkeypatch.setattr(
        metadata_shadow,
        "_load_shadow_input",
        lambda cache_key: {
            "text": TEXT,
            "filename": "unknown.docx",
            "file_path": "",
        },
    )
    monkeypatch.setattr(
        metadata_shadow,
        "_update_shadow_job",
        lambda job_id, **fields: updates.append(fields),
    )
    monkeypatch.setattr(
        "backend.rag.embedding_singleton.get_embedding",
        lambda: _StageEmbedding(),
    )

    async def _fake_shadow(*args, **kwargs):
        return CascadeDecision(
            level="L0", doc_type="legal", confidence=0.95, evidence={}
        )

    monkeypatch.setattr(
        "backend.rag.preprocessing.metadata_router.shadow_route", _fake_shadow
    )

    result = metadata_shadow_tasks.execute_metadata_shadow_task_impl("job-1")

    assert result["status"] == "succeeded"
    assert result["agreement"] is True
    assert [item["status"] for item in updates] == ["running", "succeeded"]
