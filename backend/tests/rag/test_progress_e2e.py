"""P1.5 E2E — Indexer + ProgressListener + asyncio.Queue 完整链路

模拟 '前端上传文档 → 后端 SSE 推送 6 阶段进度' 的完整流程。
不依赖 server / SSE 端点，直接验证核心逻辑：
  indexer.sync() → TraceCollector.end_span → listener → asyncio.Queue

2d627d7 重构后使用公共 fresh_collector fixture（见 tests/fixtures/sqlite_tracer.py）。
"""

import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.rag.indexing.indexer import IncrementalIndexer
from backend.observability.tracer import trace_collector, TraceCollector, WorkflowKind
from backend.observability import tracer as tracer_mod
from backend.tests.fixtures.sqlite_tracer import fresh_collector  # noqa: F401


# ==========================================================
# Fixtures
# ==========================================================


@pytest.fixture
def long_text_file(tmp_path):
    """生成一个长文本 doc.txt（避免被 ChunkFilter 全过滤）"""
    f = tmp_path / "doc.txt"
    f.write_text(
        "跨境电商平台运营规范文档。这是第一段内容，包含完整的产品上架流程说明。"
        "Amazon 平台要求所有 listing 必须包含准确的商品标题、五点描述、"
        "关键词、A+ Content 等要素。本文档详细介绍各项要求及最佳实践。"
        "第二段：广告投放规范。Sponsored Products 广告系列创建、"
        "预算分配、关键词选择、出价策略等。ACOS 控制目标 25% 以内。"
        "第三段：合规要求。FDA 认证、FCC 认证、儿童产品安全法规等。"
        * 3,
        encoding="utf-8",
    )
    return str(f)


def _build_indexer(tmp_path, file_path):
    """构造最小可用的 Indexer（mock 外部依赖）"""
    vectordb = MagicMock()
    vectordb.add_documents.return_value = ["c1", "c2", "c3"]
    vectordb._collection_name = "test_chunks"

    doc_db = MagicMock()
    doc_db.add_texts.return_value = ["d1"]

    embedding = MagicMock()
    embedding.embed_query.return_value = "fake-vector-id"

    registry = MagicMock()
    registry.list_all.return_value = {}

    return IncrementalIndexer(
        docs_dir=str(tmp_path),
        vectordb=vectordb,
        doc_db=doc_db,
        embedding=embedding,
        registry=registry,
    )


# ==========================================================
# E2E: indexer.sync() → ProgressListener → asyncio.Queue
# ==========================================================

class TestProgressListenerE2E:
    """验证 indexer 的 6 个标准 span 通过 ProgressListener 自动 emit 到 SSE queue。"""

    def test_happy_path_emits_4_sse_stages(self, fresh_collector, tmp_path, long_text_file):
        """成功索引 → 推送 parsing/chunking/embedding/writing 4 个 SSE stage。"""
        from backend.rag.progress_listener import ProgressListener

        queue: asyncio.Queue = asyncio.Queue()

        def sync_emit(stage: str, message: str = "", **extra):
            # 模拟 _do_index_sync 里的 run_coroutine_threadsafe
            queue.put_nowait({"stage": stage, "message": message, **extra})

        indexer = _build_indexer(tmp_path, long_text_file)
        listener = ProgressListener(sync_emit)
        try:
            indexer.sync()
        finally:
            listener.unsub()

        # 取出所有事件（非阻塞）
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())

        # 期望按顺序出现 8 个 stage（9 阶段里 uploading/done 由外层管理）
        stages = [e["stage"] for e in events]
        assert stages == [
            "loading", "parsing", "cleaning", "dedup",
            "chunking", "metadata", "embedding", "writing",
        ]

        # 每阶段 message 非空
        for e in events:
            assert e["message"], f"{e['stage']} message should not be empty"

        # parsing 阶段消息含 doc_count（events[1]，events[0] 是 loading）
        parsing_msg = events[1]["message"]
        assert "页" in parsing_msg  # "已解析 N 页"

        # chunking 阶段消息含 chunks 数（events[4] = loading/parsing/cleaning/dedup/chunking）
        chunking_msg = events[4]["message"]
        assert "chunks" in chunking_msg  # "切分 N chunks"

    def test_indexer_span_events_drive_listener(self, fresh_collector, tmp_path, long_text_file):
        """直接验证 span end 事件触发 listener（端到端）"""
        from backend.rag.progress_listener import ProgressListener

        received_spans = []

        def sync_emit(stage: str, message: str = "", **extra):
            received_spans.append(stage)

        indexer = _build_indexer(tmp_path, long_text_file)
        listener = ProgressListener(sync_emit)
        try:
            indexer.sync()
        finally:
            listener.unsub()

        # listener 接收了 4 个 SSE stage
        # 而 trace.spans 实际有 6+ 个（root + 4 SSE-mapped + 0 metadata）
        trace = tracer_mod.trace_collector.list(1, include_spans=True)[0]
        assert len(trace.spans) >= 5  # index_upload + parse + chunk + embed + vdb
        # listener 现在关心 SPAN_STAGE_MAP 里的 8 个（loading/parsing/cleaning/dedup/chunking/metadata/embedding/writing）
        assert received_spans == [
            "loading", "parsing", "cleaning", "dedup",
            "chunking", "metadata", "embedding", "writing",
        ]

    def test_listener_emits_metadata_stage(self, fresh_collector, tmp_path, long_text_file):
        """index_metadata span 现在单独 emit（9 阶段里 metadata 是独立阶段）"""
        from backend.rag.progress_listener import ProgressListener

        received = []

        def sync_emit(stage: str, message: str = "", **extra):
            received.append(stage)

        indexer = _build_indexer(tmp_path, long_text_file)
        listener = ProgressListener(sync_emit)
        try:
            indexer.sync()
        finally:
            listener.unsub()

        # metadata 现在在 SPAN_STAGE_MAP → 应该 emit
        assert "metadata" in received

    def test_listener_unsub_stops_future_events(self, fresh_collector, tmp_path, long_text_file):
        """unsubscribe 后不再接收后续 span 事件"""
        from backend.rag.progress_listener import ProgressListener

        received = []

        def sync_emit(stage: str, message: str = "", **extra):
            received.append(stage)

        indexer = _build_indexer(tmp_path, long_text_file)
        listener = ProgressListener(sync_emit)

        # 第一次 sync → 收到 8 个阶段（loading/parsing/cleaning/dedup/chunking/metadata/embedding/writing）
        indexer.sync()
        first_count = len(received)
        assert first_count == 8

        # 退订 → 第二次 sync 不再收到
        listener.unsub()
        indexer.sync()
        assert len(received) == first_count  # 没有增加

    def test_emit_failure_does_not_break_indexer(self, fresh_collector, tmp_path, long_text_file):
        """emit 函数异常不能影响 indexer.sync() 正常完成"""
        from backend.rag.progress_listener import ProgressListener

        def bad_emit(stage: str, message: str = "", **extra):
            raise RuntimeError("emit boom")

        indexer = _build_indexer(tmp_path, long_text_file)
        listener = ProgressListener(bad_emit)
        try:
            # sync() 应正常完成（不抛异常）
            result = indexer.sync()
        finally:
            listener.unsub()

        # sync 返回 SyncResult（added=1，因为是新文件）
        assert result.added == 1

    def test_parse_failure_propagates_to_error_stage(self, fresh_collector, tmp_path):
        """parse 失败 → SSE error 事件（虽然 ProgressListener 不直接发 error，由 _run_index_background 发）"""
        # 这个测试验证 _run_index_background 的 emit('error') 路径
        # 实际上 ProgressListener 不会发 error——error 由外层 asyncio.create_task 捕获
        # 这里只验证 listener 不会把 parse failure 变成 silent drop
        from backend.rag.progress_listener import ProgressListener

        received = []
        def sync_emit(stage, message="", **extra):
            received.append(stage)

        bad_pdf = tmp_path / "bad.pdf"
        bad_pdf.write_bytes(b"not a real pdf")

        indexer = _build_indexer(tmp_path, str(bad_pdf))
        listener = ProgressListener(sync_emit)
        try:
            with pytest.raises(RuntimeError, match="parse failed"):
                indexer.sync()
        finally:
            listener.unsub()

        # parsing 失败 → listener 收到 'parsing' 然后 trace 报 error（不影响 listener）
        # ProgressListener 只在 span end 时调用，parse span end 时 status=error → 也 emit parsing
        assert "parsing" in received


# ==========================================================
# SSE 端到端（不启 server，直接调用 stream_upload_progress）
# ==========================================================

class TestSSEStreamE2E:
    """直接调用 rag.py 的 SSE stream 函数，验证完整 event 序列。"""

    @pytest.mark.asyncio
    async def test_stream_emits_all_events_for_successful_index(
        self, fresh_collector, tmp_path, long_text_file
    ):
        """完整 SSE 流：uploading + loading + parsing + cleaning + dedup + chunking + metadata + embedding + writing + done（9 阶段）"""
        from backend.app.api.routes import _rag_shared as rag_route

        # 1. 模拟 upload_document 创建 upload_id + queue
        upload_id = "test_upload_e2e_001"
        queue: asyncio.Queue = asyncio.Queue()
        rag_route._progress_queues[upload_id] = queue

        # 2. 模拟 _run_index_background emit uploading
        await queue.put({"stage": "uploading", "message": "文件已保存"})

        # 3. 同步跑 indexer（ProgressListener 会推到 queue）
        import asyncio as _asyncio
        from backend.rag.progress_listener import ProgressListener
        from backend.rag.indexing.indexer import IncrementalIndexer
        from unittest.mock import MagicMock

        vectordb = MagicMock()
        vectordb.add_documents.return_value = ["c1", "c2", "c3"]
        doc_db = MagicMock()
        doc_db.add_texts.return_value = ["d1"]
        embedding = MagicMock()
        embedding.embed_query.return_value = "fake"
        registry = MagicMock()
        registry.list_all.return_value = {}

        indexer = IncrementalIndexer(
            docs_dir=str(tmp_path), vectordb=vectordb, doc_db=doc_db,
            embedding=embedding, registry=registry,
        )

        def sync_emit(stage, message="", **extra):
            queue.put_nowait({"stage": stage, "message": message, **extra})

        listener = ProgressListener(sync_emit)
        try:
            # run_in_executor 模拟（直接同步调用即可）
            indexer.sync()
        finally:
            listener.unsub()

        # 4. done 事件
        await queue.put({"stage": "done", "message": "索引完成",
                         "doc": {"doc_id": "abc", "file_name": "doc.txt"}})
        await queue.put(None)  # sentinel

        # 5. 收集 stream 事件（用 rag_route 的 _sse_encode 模拟）
        # 直接读取 queue 验证事件序列
        events = []
        while True:
            evt = await asyncio.wait_for(queue.get(), timeout=2.0)
            if evt is None:
                break
            events.append(evt)

        stages = [e["stage"] for e in events]
        # 期望完整序列：uploading + 8 个 listener 阶段 + done
        assert stages == [
            "uploading",   # 来自 _run_index_background
            "loading",      # listener
            "parsing",      # listener
            "cleaning",     # listener
            "dedup",        # listener
            "chunking",     # listener
            "metadata",     # listener
            "embedding",    # listener
            "writing",      # listener
            "done",         # 来自 _run_index_background
        ]


# ==========================================================
# 全链路 fixture 模拟 _run_index_background 完整流程
# ==========================================================

class TestFullBackgroundTaskE2E:
    """模拟 _run_index_background 的完整 asyncio 任务，验证 SSE 端点输出。

    关键：必须使用真实的 asyncio.run_in_executor + run_coroutine_threadsafe 路径，
    不能简化成 queue.put_nowait（用户报告 '只显示 uploading 然后关窗口' 的 bug
    可能源于真实异步路径下的事件丢失）。
    """

    @pytest.mark.asyncio
    async def test_background_task_with_real_executor_emits_all_events(
        self, fresh_collector, tmp_path, long_text_file
    ):
        """完整异步路径：主 loop → executor → run_coroutine_threadsafe → queue → SSE."""
        from backend.app.api.routes import _rag_shared as rag_route
        from backend.rag.progress_listener import ProgressListener

        upload_id = "e2e_real_executor_001"
        queue: asyncio.Queue = asyncio.Queue()
        rag_route._progress_queues[upload_id] = queue

        async def real_run_index_background():
            """复刻 rag.py _run_index_background 真实逻辑。"""
            queue_local = rag_route._progress_queues.get(upload_id)
            if queue_local is None:
                return

            async def emit(stage: str, message: str = "", **extra):
                await queue_local.put({"stage": stage, "message": message, **extra})

            try:
                # 1. emit uploading（主 loop 直接 await）
                await emit("uploading", "文件已保存，开始索引")

                # 2. run_in_executor（模拟 _do_index_sync）
                loop = asyncio.get_running_loop()

                def _do_index_sync_in_executor():
                    """在线程池里跑，对应 rag.py._do_index_sync"""
                    from unittest.mock import MagicMock
                    from backend.rag.indexing.indexer import IncrementalIndexer

                    queue_inner = rag_route._progress_queues.get(upload_id)
                    if queue_inner is None:
                        return

                    # 用 run_coroutine_threadsafe 把事件投回主 loop
                    def sync_emit(stage, message="", **extra):
                        evt = {"stage": stage, "message": message, **extra}
                        asyncio.run_coroutine_threadsafe(
                            queue_inner.put(evt), loop
                        )

                    try:
                        vectordb = MagicMock()
                        vectordb.add_documents.return_value = ["c1", "c2", "c3"]
                        doc_db = MagicMock()
                        doc_db.add_texts.return_value = ["d1"]
                        embedding = MagicMock()
                        embedding.embed_query.return_value = "fake"
                        registry = MagicMock()
                        registry.list_all.return_value = {}

                        listener = ProgressListener(sync_emit)
                        indexer = IncrementalIndexer(
                            docs_dir=str(tmp_path), vectordb=vectordb, doc_db=doc_db,
                            embedding=embedding, registry=registry,
                        )
                        try:
                            indexer.sync()
                        finally:
                            listener.unsub()
                    except Exception as e:
                        sync_emit("error", f"索引失败: {e}")
                        raise

                await loop.run_in_executor(None, _do_index_sync_in_executor)
            except Exception as e:
                await emit("error", str(e))
                await queue_local.put(None)
                return

            # 3. emit done + sentinel
            await emit("done", "索引完成",
                       doc={"doc_id": "abc", "file_name": "doc.txt"})
            await queue_local.put(None)

        # 启动后台任务
        bg_task = asyncio.create_task(real_run_index_background())

        # 模拟 SSE event_stream 消费 queue
        async def consume_sse():
            events = []
            try:
                while True:
                    evt = await asyncio.wait_for(queue.get(), timeout=30.0)
                    if evt is None:
                        break
                    events.append(evt)
                    # SSE 推送需要时间，模拟前端消费
                    await asyncio.sleep(0.01)
            except asyncio.TimeoutError:
                pass
            return events

        consume_task = asyncio.create_task(consume_sse())

        # 等待 background 完成
        await asyncio.wait_for(bg_task, timeout=30.0)
        # 等 consumer 收到 sentinel
        events = await consume_task

        # 验证完整序列
        stages = [e["stage"] for e in events]
        assert stages == [
            "uploading", "loading", "parsing", "cleaning", "dedup",
            "chunking", "metadata", "embedding", "writing", "done"
        ], f"实际序列: {stages}"

        # 清理
        del rag_route._progress_queues[upload_id]