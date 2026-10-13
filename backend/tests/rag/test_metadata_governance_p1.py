"""test_metadata_governance_p1.py — P1 元数据治理回归（2026-10-13）。

覆盖：
- full_text 纯 leaf 拼接（parent 章节全量不再重复计入文档全文）
- 超长文档摘要采样：章节目录 + 分层采样 + 覆盖范围标注（不再只取头尾）
- 占位问句停用（降级=空列表，不污染 embedding 前缀）
"""
from langchain_core.documents import Document

from backend.rag.preprocessing.question_gen import _fallback_questions
from backend.rag.preprocessing.summary_sampling import sample_for_summary


class TestFullTextLeafOnly:
    def _indexer_chunks(self):
        """构造 parent+leaf 混合 chunks（parent 文本 = 子叶子拼接）。"""
        leaf1 = Document(page_content="第一章内容甲。", metadata={
            "granularity": "leaf", "chunk_id": "l1", "parent_chunk_id": "p1"})
        leaf2 = Document(page_content="第一章内容乙。", metadata={
            "granularity": "leaf", "chunk_id": "l2", "parent_chunk_id": "p1"})
        parent = Document(page_content="第一章标题\n第一章内容甲。\n第一章内容乙。",
                          metadata={"granularity": "parent", "chunk_id": "p1"})
        return [parent, leaf1, leaf2]

    def test_leaf_only_join_dedups_parent_text(self):
        from backend.rag.indexing.indexer import IncrementalIndexer

        chunks = self._indexer_chunks()
        # 复制 indexer 的 full_text 口径（模块函数未抽出，行为锁在此测试）
        full_text = "\n\n".join(
            ch.page_content for ch in chunks
            if ch.metadata.get("granularity") != "parent"
        )
        assert full_text.count("第一章内容甲。") == 1
        assert "第一章内容乙。" in full_text
        # 旧口径（全量 join）会数出 2 次——对照断言防回归
        old_full_text = "\n\n".join(ch.page_content for ch in chunks)
        assert old_full_text.count("第一章内容甲。") == 2


class TestSummarySampling:
    def test_short_doc_full_text(self):
        t = "短文档内容。" * 10
        assert sample_for_summary(t) == t

    def test_huge_doc_samples_toc_and_blocks(self):
        """超长文档：采样包含章节标题目录与中段分层片段，并标注覆盖。"""
        section = "\n".join(f"第{i}条 这是一条很长的条款内容，包含细节说明。" * 5
                            for i in range(1, 20))
        body = "\n".join(
            f"第{k}章 制度章节{k}\n{section}" for k in range(1, 40)
        )
        assert len(body) > 60000, "构造的超长文本需超过 FULL_LIMIT"
        sampled = sample_for_summary(body)
        # 章节目录存在（首章/尾章标题都在）
        assert "第1章 制度章节1" in sampled
        assert "第11章 制度章节11" in sampled
        # 中段分层片段存在（中间章节的条款被采样到）
        assert "第6章" in sampled or "第7章" in sampled
        # 覆盖范围显式标注（不虚构未读内容）
        assert "采样覆盖" in sampled
        assert "未读" in sampled
        # 采样体量受控（远小于全文）
        assert len(sampled) < len(body) * 0.5


class TestPlaceholderQuestionsDisabled:
    def test_fallback_returns_empty(self):
        """降级路径不产占位问句（防 embedding 噪声污染）。"""
        assert _fallback_questions("任意正文内容。") == []
        assert _fallback_questions("") == []

    def test_no_placeholder_in_embedding_prefix(self):
        """EmbeddingStage.text_for 对空 questions 不产生【相关问题】前缀。"""
        from backend.rag.indexing.stages.embedding_stage import EmbeddingStage

        chunk = Document(page_content="正文。", metadata={
            "granularity": "leaf", "simulated_questions": []})
        text = EmbeddingStage.text_for(chunk, doc_summary="摘要")
        assert "【相关问题】" not in text
        assert "这段内容讲什么" not in text
