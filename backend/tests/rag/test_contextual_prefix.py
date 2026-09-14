"""1.2 Contextual Prefix — embedding 文本三级前缀 + doc 级文本增强。

契约（tests/rag/test_simulated_questions_pipeline.py 的前缀测试并行生效）：
- _embed_text_for：【文档】summary前100字 / 【章节】section_title /
  【相关问题】模拟问题，三级前缀字段缺失自动跳段，全缺则纯正文
- _build_doc_level_text：summary/章节拼入 doc 级入库文本头部，
  总长受 DOC_LEVEL_TEXT_MAX_CHARS 约束，正文最少保 2000 字
"""
import pytest
from langchain_core.documents import Document

from backend.rag.indexing.indexer import (
    DOC_LEVEL_TEXT_MAX_CHARS,
    IncrementalIndexer,
    _build_doc_level_text,
)


def _mk_indexer():
    return IncrementalIndexer.__new__(IncrementalIndexer)


class TestEmbedTextPrefix:

    def test_full_prefix_ordering(self):
        """三级前缀按 文档→章节→问题 顺序拼接，正文在最后。"""
        idx = _mk_indexer()
        chunk = Document(page_content="正文内容", metadata={
            "section_title": "报销标准",
            "simulated_questions": ["报销上限是多少？", "怎么申请？"],
        })
        text = idx._embed_text_for(chunk, doc_summary="财务报销制度说明文档")
        assert text.startswith("【文档】财务报销制度说明文档")
        assert "【章节】报销标准" in text
        assert "【相关问题】报销上限是多少？ | 怎么申请？" in text
        assert text.endswith("正文内容")

    def test_missing_fields_skip_segments(self):
        """缺章节或缺问题 → 对应段跳过，不残留空段标记。"""
        idx = _mk_indexer()
        chunk = Document(page_content="正文", metadata={
            "simulated_questions": ["Q1"],
        })
        text = idx._embed_text_for(chunk, doc_summary="摘要")
        assert "【文档】摘要" in text
        assert "【章节】" not in text
        assert "【相关问题】Q1" in text

    def test_all_missing_returns_plain_text(self):
        """全缺 → 纯正文（向后兼容：无 metadata 的 chunk 行为不变）。"""
        idx = _mk_indexer()
        chunk = Document(page_content="裸正文", metadata={})
        assert idx._embed_text_for(chunk) == "裸正文"
        assert idx._embed_text_for(chunk, doc_summary="") == "裸正文"

    def test_summary_truncated_to_100(self):
        """summary 前 100 字截断——避免前缀喧宾夺主。"""
        idx = _mk_indexer()
        chunk = Document(page_content="正文", metadata={})
        text = idx._embed_text_for(chunk, doc_summary="长" * 300)
        prefix_line = text.split("\n")[0]
        assert prefix_line == "【文档】" + "长" * 100

    def test_section_title_whitespace_only_skipped(self):
        """section_title 为空白串 → 视为缺失跳段。"""
        idx = _mk_indexer()
        chunk = Document(page_content="正文", metadata={"section_title": "   "})
        assert idx._embed_text_for(chunk) == "正文"

    def test_embed_with_retry_propagates_summary(self):
        """_embed_with_retry 的 doc_summary 参数传播到每条嵌入文本。"""
        import backend.rag.indexing.indexer as indexer_mod
        from unittest.mock import MagicMock

        seen = []
        emb = MagicMock()
        emb.embed_documents.side_effect = lambda texts: (
            seen.extend(texts), [[0.0]] * len(texts))[1]
        idx = _mk_indexer()
        idx.embedding = emb

        chunks = [Document(page_content=f"c{i}", metadata={}) for i in range(2)]
        idx._embed_with_retry(chunks, parent_span=None, doc_summary="制度总述")
        assert all(t.startswith("【文档】制度总述") for t in seen)


class TestDocLevelText:

    def test_header_prepended(self):
        full = "正文" * 5000
        text = _build_doc_level_text(full, {"summary": "报销制度", "sections": ["总则", "标准"]})
        assert text.startswith("报销制度\n章节：总则、标准\n\n正文")

    def test_total_length_bounded(self):
        full = "字" * (DOC_LEVEL_TEXT_MAX_CHARS * 2)
        meta = {"summary": "摘" * 200, "sections": [f"章{i}" for i in range(20)]}
        text = _build_doc_level_text(full, meta)
        assert len(text) <= DOC_LEVEL_TEXT_MAX_CHARS + 50  # 少量余量防边界差一

    def test_body_min_budget(self):
        """header 超长时正文至少保 2000 字，不被挤没。"""
        full = "正" * 5000
        meta = {"summary": "长" * 15000}
        text = _build_doc_level_text(full, meta)
        body = text.split("\n\n", 1)[1]
        assert len(body) >= 2000

    def test_empty_meta_falls_back_to_raw(self):
        """无 summary/sections → 行为与旧实现一致（裸全文头 16K）。"""
        full = "字" * 100
        assert _build_doc_level_text(full, {}) == full
        assert _build_doc_level_text(full, {"summary": "", "sections": []}) == full

    def test_empty_text_returns_empty(self):
        assert _build_doc_level_text("", {"summary": "x"}) == ""

    def test_truncated_mark_still_applies(self):
        """超长文档的截断打标依据（full_text 长度）不受 header 影响——
        调用方在 indexer 里以 full_text 判定 doc_level_truncated，本函数不改语义。"""
        full = "字" * (DOC_LEVEL_TEXT_MAX_CHARS + 1)
        text = _build_doc_level_text(full, {})
        assert len(text) == DOC_LEVEL_TEXT_MAX_CHARS
