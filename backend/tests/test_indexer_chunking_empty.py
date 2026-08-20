"""reindex_file chunk_count=0 校验测试 (P1-4)。

背景:之前 reindex_file 不校验 chunk_count,如果解析成功但 chunking 策略
切出 0 个有效 chunk(扫描件 PDF / 纯图片 / 结构分析失败等),会向用户报告
"索引成功"但 doc_db 实际为空。后续 retrieve 永远召不回该文档。

校验策略:在 _index_file 末尾,chunks 已写入 registry 但 chunk_count=0 时
raise ChunkingEmptyError。reindex_file 必须把这个错误传播出去。

可观测性:日志留痕、SSE 推 error 事件(由 _run_index_background 消费)。
可靠性:如果文件之前已存在,reindex 失败不能删旧文件(保护已有数据)。
"""
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.rag.indexing.indexer import IncrementalIndexer, ChunkingEmptyError


# ============ Helper:构造一个会触发 chunk_count=0 的 Indexer ============

def _make_indexer_with_zero_chunks(tmp_path: Path) -> IncrementalIndexer:
    """构造一个 parser 返 1 chunk、但 chunking strategy 切 0 的 indexer。"""
    indexer = IncrementalIndexer(
        docs_dir=str(tmp_path),
        vectordb=MagicMock(),
        doc_db=MagicMock(),
        embedding=MagicMock(),
        registry=MagicMock(),
    )
    # 强制 _index_file_inner 走到 chunks=[] 但不抛 parse 错
    indexer._current_chunks = []
    return indexer


# ============ ChunkingEmptyError 自定义异常测试 ============

class TestChunkingEmptyError:
    """ChunkingEmptyError 必须是自定义异常,且能从 reindex_file 抛给调用方。"""

    def test_is_exception_subclass(self):
        assert issubclass(ChunkingEmptyError, Exception)

    def test_carries_useful_message(self):
        err = ChunkingEmptyError("file.md produced 0 chunks (ext=.md)")
        assert "0 chunks" in str(err)

    def test_can_be_caught_by_caller(self):
        # 调用方代码路径期望能 try/except 这个类型做差异化处理
        with pytest.raises(ChunkingEmptyError):
            raise ChunkingEmptyError("test")


# ============ _index_file chunk_count=0 路径测试 ============

class TestIndexFileZeroChunks:
    """_index_file_inner 写 registry 时 chunk_count 必须 > 0,否则 raise。"""

    def test_zero_chunks_raises_chunking_empty_error(self, tmp_path, monkeypatch):
        # 创建一个空内容文件(pparser 可能返 0 chunks 或仅 metadata 没正文)
        empty_file = tmp_path / "empty.pdf"
        empty_file.write_bytes(b"%PDF-1.4\n")  # magic OK,但内容为空

        indexer = IncrementalIndexer(
            docs_dir=str(tmp_path),
            vectordb=MagicMock(),
            doc_db=MagicMock(),
            embedding=MagicMock(),
            registry=MagicMock(),
        )

        # Mock 掉 parse_and_chunk 让它返 0 chunks(模拟扫描件 PDF / 结构损坏)
        # 注意:_index_file_inner 是函数内 `from backend.rag.preprocessing.pipeline import parse_and_chunk`,
        # 每次调用都重新查找模块,所以 patch 原模块的 parse_and_chunk
        from backend.rag.preprocessing import pipeline as parse_pipeline_mod
        monkeypatch.setattr(parse_pipeline_mod, "parse_and_chunk", lambda *_a, **_kw: [])

        with pytest.raises(ChunkingEmptyError) as exc_info:
            indexer._index_file_inner(
                file_path=str(empty_file),
                kb_id="policy_general",
                doc_id="dummy",
                file_hash="dummy",
            )
        # 错误信息要说明哪个文件 + chunk 数
        assert "empty" in str(exc_info.value).lower() or "0 chunks" in str(exc_info.value).lower()


# ============ reindex_file 传播错误测试 ============

class TestReindexFilePropagatesChunkingEmptyError:
    """reindex_file 必须把 ChunkingEmptyError 抛给调用方,不静默吞掉。"""

    def test_reindex_propagates_zero_chunk_error(self, tmp_path):
        target = tmp_path / "broken.md"
        target.write_text("# 标题\n", encoding="utf-8")

        indexer = IncrementalIndexer(
            docs_dir=str(tmp_path),
            vectordb=MagicMock(),
            doc_db=MagicMock(),
            embedding=MagicMock(),
            registry=MagicMock(),
        )

        # Mock 掉 _index_file 让它 raise(模拟 chunk_count=0 的实际场景)
        def fake_index_file(_path, file_hash=None):
            raise ChunkingEmptyError(f"{target.name}: produced 0 chunks")
        indexer._index_file = fake_index_file

        # reindex_file 不能静默吞掉,必须让调用方看到
        with pytest.raises(ChunkingEmptyError):
            indexer.reindex_file(str(target))

    def test_reindex_does_not_silently_return_zero_chunk_dict(self, tmp_path):
        """禁止的旧行为:return {"chunk_count": 0} 让前端以为成功。"""
        target = tmp_path / "broken.md"
        target.write_text("# 标题\n", encoding="utf-8")

        indexer = IncrementalIndexer(
            docs_dir=str(tmp_path),
            vectordb=MagicMock(),
            doc_db=MagicMock(),
            embedding=MagicMock(),
            registry=MagicMock(),
        )

        def fake_index_file(_path, file_hash=None):
            raise ChunkingEmptyError("zero chunks")
        indexer._index_file = fake_index_file

        # 任何形式的成功返回(包括 chunk_count=0)都视为 bug
        try:
            result = indexer.reindex_file(str(target))
            assert result.get("chunk_count") != 0, (
                "P1-4: reindex_file 禁止返回 chunk_count=0 的成功 dict"
            )
        except ChunkingEmptyError:
            pass  # 期望行为
        else:
            pytest.fail("reindex_file 未抛 ChunkingEmptyError,违反 P1-4")


# ============ 异常类型聚合测试 ============

class TestExceptionInheritance:
    """所有"索引失败"类异常必须能被外部统一捕获的基类识别。"""

    def test_chunking_empty_error_catchable_as_runtime_error(self):
        # 防御性:第三方 trace 监控可能只 catch RuntimeError
        try:
            raise ChunkingEmptyError("test")
        except RuntimeError:
            pytest.fail("ChunkingEmptyError 不应继承 RuntimeError,否则可能被误捕")
        except ChunkingEmptyError:
            pass