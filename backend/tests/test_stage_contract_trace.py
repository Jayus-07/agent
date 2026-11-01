"""治理 A：检索管线阶段契约单测

锁定三个中间阶段的驱逐/决策留痕：
  1. Stage1 文档门控（matched / similarity_top / excluded+理由）
  2. AdaptiveRetriever 扩展（cluster 触发 / 替换 / 保留）
  3. RerankCompressor 驱逐明细
验证手段：返回值断言 + caplog 日志断言（无 active trace 时 span 为 noop，
日志是始终可用的契约出口）。
"""
import logging

from langchain_core.documents import Document

from backend.rag.retrieval.retrievers import ChunkLevelRetriever, AdaptiveRetriever


# ==================== 1. Stage1 门控契约 ====================

def _doc(doc_id: str, source: str, keywords: str = "") -> Document:
    """keywords 用与真实注册表一致的 JSON 数组字符串格式。"""
    return Document(page_content="x", metadata={
        "doc_id": doc_id, "source": source, "doc_keywords": keywords,
    })


class TestStage1GateInfo:
    """50941f5 起关键词门控直通（P0-2 治理）：候选全保留，排除链退役。

    旧「关键词 ∩ doc_keywords 硬过滤」语义整体退役，本组锁定直通契约：
    gate_info 契约形状（kept/excluded/reason）必须与实现保持兼容。
    """

    def test_candidates_all_retained_regardless_of_keywords(self):
        """无关键词/零交集文档不再被驱逐——硬门控根除回归守卫。"""
        docs = [
            _doc("d1", "物流SOP.docx", '["跨境", "运输"]'),
            _doc("d2", "数字陷阱.md", ""),  # 无关键词，旧实现靠相似度兜底
            _doc("d3", "采购合同.md", '["合同", "采购"]'),
            _doc("d4", "技术手册.pdf", ""),  # 旧实现会被排除的第 4 篇
        ]
        ids, info = ChunkLevelRetriever._filter_docs_by_keywords("跨境运输有哪些方式", docs)
        assert ids == ["d1", "d2", "d3", "d4"]
        assert info["kept"] == ["d1", "d2", "d3", "d4"]
        assert info["excluded"] == []

    def test_gate_info_contract_shape(self):
        """gate_info 契约字段保留：candidates 计数 + reason 标记直通口径。"""
        docs = [_doc("d1", "a.pdf"), _doc("d2", "b.md")]
        ids, info = ChunkLevelRetriever._filter_docs_by_keywords("？？？", docs)
        assert set(ids) == {"d1", "d2"}
        assert info["candidates"] == 2
        assert info["reason"] == "keyword_gate_removed_p0_2"
        # 已退役字段显式置空，供 Trace 消费方兼容
        assert info["keyword_matched"] is None
        assert info["similarity_fallback"] == 0


# ==================== 2. Adaptive 扩展契约 ====================

import pytest  # noqa: E402
from langchain_core.retrievers import BaseRetriever  # noqa: E402
from langchain_core.callbacks import CallbackManagerForRetrieverRun  # noqa: E402
from typing import List  # noqa: E402


@pytest.fixture(autouse=True)
def _bypass_review_visibility(monkeypatch):
    """AdaptiveRetriever 出口过审核可见性过滤（fake chunk 未登记 doc_registry，
    CI 空库 fail-closed → 空结果，v14 run 41249581757）。本组锁扩展契约，
    审核语义由 review_filter 自有测试覆盖——透传该出口。"""
    import backend.rag.retrieval.review_filter as rf

    monkeypatch.setattr(rf, "apply_review_visibility", lambda docs, **kw: docs)


class _FakeBaseRetriever(BaseRetriever):
    """鸭子类型 base_retriever：AdaptiveRetriever 只调 invoke(query)。"""

    chunks: list = []

    def _get_relevant_documents(self, query: str, *, run_manager=None) -> List[Document]:
        return list(self.chunks)


class _FakeDocDB:
    def __init__(self, full_docs: dict, source_of=None):
        self._full = full_docs
        self._source_of = source_of or {}

    def get(self, where=None):
        ids = where["doc_id"]["$in"]
        documents, metadatas = [], []
        for did in ids:
            if did in self._full:
                documents.append(self._full[did])
                metadatas.append({"doc_id": did, "source": self._source_of.get(did, f"full_{did}")})
        return {"documents": documents, "metadatas": metadatas}


class TestAdaptiveExpansionContract:
    def _chunk(self, doc_id: str, source: str, score: float = 0.8) -> Document:
        return Document(page_content="chunk 内容，长度足够", metadata={
            "doc_id": doc_id, "source": source, "similarity": score,
        })

    def test_expansion_replaces_cluster_and_logs(self, caplog):
        chunks = [self._chunk("A", "a.pdf"), self._chunk("A", "a.pdf"),
                  self._chunk("A", "a.pdf"), self._chunk("B", "b.md")]
        adaptive = AdaptiveRetriever(
            base_retriever=_FakeBaseRetriever(chunks=chunks),
            doc_db=_FakeDocDB({"A": "A 全文"}, source_of={"A": "a.pdf"}),
        )
        with caplog.at_level(logging.INFO, logger="rag_system"):
            result = adaptive.invoke("问题")
        # cluster doc A 被全文替换为 1 条（page_content=A 全文），B 原位保留
        assert len(result) == 2
        assert any(d.page_content == "A 全文" for d in result)
        assert any(d.metadata.get("source") == "b.md" for d in result)
        assert any("扩展替换" in r.message and "a.pdf" in r.message for r in caplog.records)

    def test_low_confidence_skip_logged(self, caplog):
        chunks = [self._chunk("A", "a.pdf", score=0.2),
                  self._chunk("A", "a.pdf", score=0.1),
                  self._chunk("A", "a.pdf", score=0.1),
                  self._chunk("B", "b.md", score=0.1)]
        adaptive = AdaptiveRetriever(
            base_retriever=_FakeBaseRetriever(chunks=chunks),
            doc_db=_FakeDocDB({"A": "A 全文"}, source_of={"A": "a.pdf"}),
        )
        with caplog.at_level(logging.INFO, logger="rag_system"):
            result = adaptive.invoke("问题")
        assert result == chunks  # 原样透传
        assert any("跳过 Expansion" in r.message for r in caplog.records)


# ==================== 3. Rerank 驱逐明细 ====================

class TestRerankEvictionLog:
    def test_evicted_docs_logged(self, caplog):
        from backend.rag.reranker import RerankCompressor

        comp = RerankCompressor()
        docs = [
            Document(page_content=f"内容{i}" * 10, metadata={"source": f"d{i}.pdf"})
            for i in range(5)
        ]

        class FakeBackend:
            def compress_documents(self, documents, query, top_k=3, threshold=0.3):
                # 只保留前 2 篇，模拟驱逐
                return documents[:2]

        # backend/_backend_type 是 pydantic 未声明字段的非 field 属性
        # （__init__ 里经 self.__dict__ 写入），setattr 会被 pydantic 拒绝
        comp.__dict__["backend"] = FakeBackend()
        comp.__dict__["_backend_type"] = "local"

        with caplog.at_level(logging.INFO, logger="rag_system"):
            out = comp.compress_documents(docs, "问题")

        assert len(out) == 2
        evict_logs = [r for r in caplog.records if "[Rerank] 淘汰" in r.message]
        assert evict_logs, "驱逐未留痕"
        assert "d2.pdf" in evict_logs[0].message
