"""TD-01 向量路降级信号回归（2026-10-02）。

实测背景：DashScope embedding 间歇 Connection error 时，RRF 融合只剩
BM25 路、分数塌陷到 0.065 量级，AdaptiveRetriever 的"低置信跳过
Expansion"在残缺分数上误判 → parent 上下文未补全 → leaf 碎片缺标题
实体 → 实体覆盖校验假拒答。本文件钉死三个行为：
  1. 全部 query 的 embedding 检索失败 → 置 vector_degraded 信号；
  2. 部分成功（原始 query 成功）→ 不置信号；
  3. vector_degraded 置位时，AdaptiveRetriever 低置信分数不再跳过
     Context Expansion（未置位时保持原跳过行为）。
"""
import pytest

from backend.rag.base import CustomRetriever
from backend.rag.context import (
    clear_context,
    get_context,
    is_vector_degraded,
    set_context,
)
from backend.rag.retrieval.retrievers import AdaptiveRetriever
from backend.rag.retrieval.retrievers import _Staging  # noqa: F401  确认模块可导入


class _FlakyVectordb:
    """原始 query 成功、扩展 query 抛连接错误（模拟部分故障）。"""

    def __init__(self, fail_all: bool = True):
        self.fail_all = fail_all
        self.calls = 0

    def similarity_search_with_score(self, query, k=5, filter=None):  # noqa: A002
        self.calls += 1
        if self.fail_all:
            raise ConnectionError("Connection error.")
        if self.calls == 1:
            from langchain_core.documents import Document

            return [
                (
                    Document(
                        page_content=f"chunk {i}",
                        metadata={"chunk_id": f"c{i}", "doc_id": "d1", "chunk_index": i},
                    ),
                    0.9 - i * 0.01,
                )
                for i in range(k)
            ]
        raise ConnectionError("Connection error.")


class _Sig:
    """记录调用断言用的哨兵。"""


def _make_retriever(fail_all: bool) -> CustomRetriever:
    return CustomRetriever(_FlakyVectordb(fail_all=fail_all))


def test_all_queries_fail_marks_vector_degraded():
    """全部 query embedding 失败 → vector_degraded 置位。"""
    set_context(
        __import__("backend.rag.context", fromlist=["RagRequestState"]).RagRequestState(
            metadata_filter={}, intent_label="", query=""
        )
    )
    try:
        assert is_vector_degraded() is False
        r = _make_retriever(fail_all=True)
        docs = r.retrieve("差旅住宿费标准", k=3, expanded_queries=["差旅 住宿 限额"])
        assert docs == []  # 无任何向量结果
        assert is_vector_degraded() is True
    finally:
        clear_context()


def test_partial_success_keeps_signal_off():
    """原始 query 成功、扩展失败 → 不置降级信号（向量分存在，尺度正常）。"""
    set_context(
        __import__("backend.rag.context", fromlist=["RagRequestState"]).RagRequestState(
            metadata_filter={}, intent_label="", query=""
        )
    )
    try:
        r = _make_retriever(fail_all=False)
        docs = r.retrieve("差旅住宿费标准", k=3, expanded_queries=["差旅 住宿 限额"])
        assert len(docs) == 3
        assert is_vector_degraded() is False
    finally:
        clear_context()


class _GateProbe:
    """记录门控决策的探针（替代真实 doc_db / trace）。"""

    def __init__(self):
        self.expansion_attempted = False


def _build_adaptive(monkeypatch, *, degraded: bool, scores: list[float]) -> tuple:
    """构造 AdaptiveRetriever + 捕获扩展分支。score 来源 rrf_score。"""
    from langchain_core.documents import Document

    chunks = [
        Document(
            page_content=f"chunk {i}",
            metadata={"doc_id": "d1", "chunk_index": i, "rrf_score": s},
        )
        for i, s in enumerate(scores)
    ]
    probe = _GateProbe()

    class _Base:
        """pydantic 校验要求 BaseRetriever 实例——用最小说明覆盖。"""

        def is_lc_serializable(self):
            return False

        def get_relevant_documents(self, query, **kwargs):
            return chunks

    from langchain_core.retrievers import BaseRetriever as _LCBaseRetriever

    class _StubRetriever(_LCBaseRetriever):
        def _get_relevant_documents(self, query, *, run_manager=None, **kwargs):
            return chunks

    retriever = AdaptiveRetriever(
        base_retriever=_StubRetriever(),
        doc_db=_FakeDocDB(),
        cluster_threshold=0.5,
        max_cluster_docs=3,
    )
    # 置/清降级信号
    set_context(
        __import__("backend.rag.context", fromlist=["RagRequestState"]).RagRequestState(
            metadata_filter={}, intent_label="", query=""
        )
    )
    if degraded:
        from backend.rag.context import mark_vector_degraded
        mark_vector_degraded()
    return retriever, probe


class _FakeDocDB:
    def get(self, where=None):
        return {
            "documents": ["全文标题：差旅费报销管理制度 一类城市 600 元/晚"],
            "metadatas": [{"doc_id": "d1"}],
        }


def test_degraded_low_scores_still_expand(monkeypatch):
    """降级 + 低分（0.065）→ 不跳过，执行 Context Expansion。"""
    retriever, probe = _build_adaptive(monkeypatch, degraded=True, scores=[0.065, 0.066])
    docs = retriever._get_relevant_documents("差旅住宿费标准")
    # 扩展发生：full doc（含标题）替换 chunk 进入结果
    assert any("差旅费报销管理制度" in d.page_content for d in docs)
    clear_context()


def test_normal_low_scores_skip_expand(monkeypatch):
    """未降级 + 同样的低分 → 保持原行为：跳过 Expansion。"""
    retriever, _probe = _build_adaptive(monkeypatch, degraded=False, scores=[0.065, 0.066])
    docs = retriever._get_relevant_documents("差旅住宿费标准")
    assert all("差旅费报销管理制度" not in d.page_content or d.metadata.get("chunk_index") is not None
               for d in docs)  # 仅有原 chunks，未注入 full doc
    clear_context()
