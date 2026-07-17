"""P1.1 — backend/rag/indexing/indexer.py 的 trace 集成测试

覆盖 Knowledge Index Trace 6 个标准 span:
  index_upload → index_parse → index_chunk → index_embed → index_vector_db → index_metadata
+ failure paths (parse fail / embed retry 全失败)
"""

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.rag.indexing.indexer import IncrementalIndexer
from backend.rag.tracer import (
    trace_collector,
    TraceCollector,
    WorkflowKind,
    SpanKind,
)


# ==========================================================
# Fixtures
# ==========================================================

@pytest.fixture
def fresh_collector():
    """重置全局 trace_collector 的状态（deque + _active + 字段）。

    indexer.py 用 `from ... import trace_collector` 绑定模块级对象，无法替换；
    改为每个测试前后清空全局单例的 records，避免污染。
    """
    from backend.rag import tracer as tracer_mod

    saved_records = list(tracer_mod.trace_collector._records)
    saved_active = set(tracer_mod.trace_collector._active)
    saved_timers = dict(tracer_mod.trace_collector._timers)
    saved_thread_current = tracer_mod.trace_collector._thread_current
    saved_span_seq = tracer_mod.trace_collector._span_seq

    tracer_mod.trace_collector.clear()

    yield tracer_mod.trace_collector

    # restore
    tracer_mod.trace_collector._records.clear()
    tracer_mod.trace_collector._records.extend(saved_records)
    tracer_mod.trace_collector._active = saved_active
    tracer_mod.trace_collector._timers = saved_timers
    tracer_mod.trace_collector._thread_current = saved_thread_current
    tracer_mod.trace_collector._span_seq = saved_span_seq


@pytest.fixture
def tmp_text_file(tmp_path):
    """写一个简单的 .txt 文件供 indexer 加载。"""
    f = tmp_path / "doc.txt"
    f.write_text("这是测试文档内容。" * 10, encoding="utf-8")
    return str(f)


@pytest.fixture
def indexer(tmp_path, tmp_text_file):
    """构造一个最小可用的 IncrementalIndexer，全 mock 外部依赖。"""
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


def _find_span(trace, span_id):
    """按 span_id 查找 Span，未找到则 raise。"""
    for s in trace.spans:
        if s.span_id == span_id:
            return s
    raise AssertionError(f"span '{span_id}' not found in trace (have: {[s.span_id for s in trace.spans]})")


# ==========================================================
# 1. 正常路径 — 6 个 span 全部创建并 close
# ==========================================================

class TestHappyPath:
    def test_root_span_kind_is_workflow_index_upload(self, indexer, tmp_text_file, fresh_collector):
        indexer._index_file(tmp_text_file)
        trace = fresh_collector.list()[0]

        assert trace.workflow_kind == WorkflowKind.KNOWLEDGE_INDEX.value
        assert trace.workflow_name == "knowledge_index"
        assert trace.tags["doc_id"]  # 非空
        assert trace.tags["kb_id"] == "default"  # 在 tmp_path 根目录下，kb_id=default

        root = _find_span(trace, "index_upload")
        assert root.kind == SpanKind.INDEX_UPLOAD.value
        assert root.parent_id is None
        assert root.status == "success"

    def test_all_six_spans_present(self, indexer, tmp_text_file, fresh_collector):
        indexer._index_file(tmp_text_file)
        trace = fresh_collector.list()[0]

        expected = {"index_upload", "index_parse", "index_chunk",
                    "index_embed", "index_vector_db", "index_metadata"}
        actual = {s.span_id for s in trace.spans}
        assert expected.issubset(actual), f"missing: {expected - actual}"

    def test_all_spans_have_correct_kind(self, indexer, tmp_text_file, fresh_collector):
        indexer._index_file(tmp_text_file)
        trace = fresh_collector.list()[0]

        kind_map = {
            "index_upload": SpanKind.INDEX_UPLOAD.value,
            "index_parse": SpanKind.INDEX_PARSE.value,
            "index_chunk": SpanKind.INDEX_CHUNK.value,
            "index_embed": SpanKind.INDEX_EMBED.value,
            "index_vector_db": SpanKind.INDEX_VECTOR_DB.value,
            "index_metadata": SpanKind.INDEX_METADATA.value,
        }
        for sid, expected_kind in kind_map.items():
            span = _find_span(trace, sid)
            assert span.kind == expected_kind, f"{sid} kind mismatch"

    def test_child_spans_parent_is_upload(self, indexer, tmp_text_file, fresh_collector):
        indexer._index_file(tmp_text_file)
        trace = fresh_collector.list()[0]

        for sid in ("index_parse", "index_chunk", "index_embed",
                    "index_vector_db", "index_metadata"):
            span = _find_span(trace, sid)
            assert span.parent_id == "index_upload", f"{sid} parent mismatch"

    def test_all_spans_status_success(self, indexer, tmp_text_file, fresh_collector):
        indexer._index_file(tmp_text_file)
        trace = fresh_collector.list()[0]
        for s in trace.spans:
            assert s.status == "success", f"{s.span_id} status={s.status}"

    def test_all_spans_have_duration(self, indexer, tmp_text_file, fresh_collector):
        indexer._index_file(tmp_text_file)
        trace = fresh_collector.list()[0]
        for s in trace.spans:
            assert s.duration_ms >= 0, f"{s.span_id} duration not recorded"


# ==========================================================
# 2. 各 span 的 metrics 字段
# ==========================================================

class TestSpanMetrics:
    def test_parse_metrics_records_doc_count(self, indexer, tmp_text_file, fresh_collector):
        indexer._index_file(tmp_text_file)
        trace = fresh_collector.list()[0]
        parse = _find_span(trace, "index_parse")
        assert parse.metrics["doc_count"] >= 1

    def test_chunk_metrics_records_kept_and_filtered(self, indexer, tmp_text_file, fresh_collector):
        indexer._index_file(tmp_text_file)
        trace = fresh_collector.list()[0]
        chunk = _find_span(trace, "index_chunk")
        assert "kept_chunks" in chunk.metrics
        assert "filtered_out" in chunk.metrics
        assert chunk.metrics["kept_chunks"] >= 1

    def test_embed_metrics_records_succeeded_count(self, indexer, tmp_text_file, fresh_collector):
        indexer._index_file(tmp_text_file)
        trace = fresh_collector.list()[0]
        embed = _find_span(trace, "index_embed")
        assert "attempted" in embed.metrics
        assert "succeeded" in embed.metrics
        assert embed.metrics["succeeded"] == embed.metrics["attempted"]

    def test_vector_db_metrics_records_written(self, indexer, tmp_text_file, fresh_collector):
        indexer._index_file(tmp_text_file)
        trace = fresh_collector.list()[0]
        vdb = _find_span(trace, "index_vector_db")
        assert vdb.metrics["written"] == 3  # mock 返回 ["c1", "c2", "c3"]

    def test_metadata_metrics_records_doc_type(self, indexer, tmp_text_file, fresh_collector):
        indexer._index_file(tmp_text_file)
        trace = fresh_collector.list()[0]
        meta = _find_span(trace, "index_metadata")
        assert "doc_type" in meta.metrics


# ==========================================================
# 3. 异常路径
# ==========================================================

class TestFailurePaths:
    def test_parse_failure_marks_span_error(self, tmp_path, fresh_collector):
        """PDF 加载失败 → index_parse 标 error，但 index_upload 也应标 error"""
        import backend.rag.tracer as tracer_mod
        tracer_mod.trace_collector = fresh_collector

        # 构造 .pdf 扩展名但内容损坏
        bad_pdf = tmp_path / "bad.pdf"
        bad_pdf.write_bytes(b"not a real pdf")

        vectordb = MagicMock()
        doc_db = MagicMock()
        embedding = MagicMock()
        registry = MagicMock()
        registry.list_all.return_value = {}

        indexer = IncrementalIndexer(
            docs_dir=str(tmp_path), vectordb=vectordb,
            doc_db=doc_db, embedding=embedding, registry=registry,
        )
        with pytest.raises(RuntimeError, match="parse failed"):
            indexer._index_file(str(bad_pdf))

        trace = fresh_collector.list()[0]
        parse_span = _find_span(trace, "index_parse")
        assert parse_span.status == "error"
        assert "error" in parse_span.metrics

        root = _find_span(trace, "index_upload")
        assert root.status == "error"

    def test_embed_all_retries_fail_marks_chunk_span_error(self, tmp_path, fresh_collector):
        """嵌入服务一直失败 → embed_chunk_i 子 span 标 error + retry_count=3"""
        import backend.rag.tracer as tracer_mod
        tracer_mod.trace_collector = fresh_collector

        f = tmp_path / "doc.txt"
        # 内容必须足够长，否则 ChunkFilter 会过滤掉 → embed span 不创建 chunk
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

        vectordb = MagicMock()
        doc_db = MagicMock()
        embedding = MagicMock()
        embedding.embed_query.side_effect = RuntimeError("embed service down")
        registry = MagicMock()
        registry.list_all.return_value = {}

        indexer = IncrementalIndexer(
            docs_dir=str(tmp_path), vectordb=vectordb,
            doc_db=doc_db, embedding=embedding, registry=registry,
        )
        indexer._index_file(str(f))

        trace = fresh_collector.list()[0]
        # embed span 仍创建，但 succeeded=0, failed=N
        embed = _find_span(trace, "index_embed")
        assert embed.metrics["succeeded"] == 0
        assert embed.metrics["failed"] >= 1

        # 至少有一个 embed_chunk_* 子 span 标 error
        chunk_error_spans = [
            s for s in trace.spans
            if s.span_id.startswith("embed_chunk_") and s.status == "error"
        ]
        assert len(chunk_error_spans) >= 1
        assert chunk_error_spans[0].retry_count == 3
        assert "error" in chunk_error_spans[0].metrics

    def test_embed_succeeds_after_retry(self, tmp_path, fresh_collector):
        """第 2 次重试成功 → retry_count=1, status=success"""
        import backend.rag.tracer as tracer_mod
        tracer_mod.trace_collector = fresh_collector

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

        vectordb = MagicMock()
        doc_db = MagicMock()
        embedding = MagicMock()
        # 第一次失败，第二次成功
        embedding.embed_query.side_effect = [RuntimeError("transient"), "id1"]
        registry = MagicMock()
        registry.list_all.return_value = {}

        indexer = IncrementalIndexer(
            docs_dir=str(tmp_path), vectordb=vectordb,
            doc_db=doc_db, embedding=embedding, registry=registry,
        )
        indexer._index_file(str(f))

        trace = fresh_collector.list()[0]
        chunk_span = _find_span(trace, "embed_chunk_0")
        assert chunk_span.status == "success"
        assert chunk_span.metrics["attempt"] == 2


# ==========================================================
# 4. Workflow 路由 — TraceRecord.workflow_kind 区分
# ==========================================================

class TestWorkflowRouting:
    def test_indexer_trace_kind_is_knowledge_index(self, indexer, tmp_text_file, fresh_collector):
        indexer._index_file(tmp_text_file)
        trace = fresh_collector.list()[0]
        assert trace.workflow_kind == "knowledge_index"
        assert trace.workflow_kind != "rag_query"  # 与 RAG 区分

    def test_indexer_trace_tags_carry_doc_metadata(self, indexer, tmp_text_file, fresh_collector):
        indexer._index_file(tmp_text_file)
        trace = fresh_collector.list()[0]
        assert trace.tags["kb_id"]  # 非空
        assert trace.tags["doc_id"]  # 非空
        assert trace.tags["file_ext"] == ".txt"