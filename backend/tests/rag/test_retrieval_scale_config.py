"""RAG 规模化检索配置回归测试。

这些测试只隔离 BM25 磁盘目录和文档级检索对象，验证候选池配置确实
穿过调用边界；不依赖真实 PostgreSQL、embedding 服务或外部模型。
"""

from types import SimpleNamespace

from langchain_core.documents import Document


def test_doc_stage_uses_configured_candidate_k(monkeypatch):
    """文档级 Stage 1 不应被固定的 15 个候选限制。"""
    import backend.rag.retrieval.retrievers as retrievers_module

    calls = []

    def similarity_search(query, k=5, filter=None):
        calls.append({"query": query, "k": k, "filter": filter})
        return [Document(page_content="doc", metadata={"doc_id": "d1"})]

    monkeypatch.setattr(retrievers_module, "RAG_DOC_CANDIDATE_K", 50)
    retriever = retrievers_module.ChunkLevelRetriever(
        doc_db=SimpleNamespace(similarity_search=similarity_search),
        vectordb=None,
        chunk_retriever=None,
        bm25=None,
        person_index={},
    )
    staging = retrievers_module._Staging(query="问题", span=None)

    retriever._authorized_doc_search(staging)

    assert calls == [{"query": "问题", "k": 50, "filter": None}]


def test_bm25_store_default_uses_candidate_k(tmp_path, monkeypatch):
    """BM25 默认返回候选池大小，而不是最终答案条数。"""
    import backend.rag.retrieval.bm25_store as bm25_module

    monkeypatch.setattr(bm25_module, "BM25_CANDIDATE_K", 100)
    store = bm25_module.BM25Store(tmp_path / "bm25")

    retriever = store.build([
        Document(page_content="退款政策 七天", metadata={"doc_id": "d1"})
    ])

    assert retriever is not None
    assert retriever.k == 100


def test_bm25_metadata_filter_matches_json_array_metadata():
    """BM25 结果过滤要与 PGVector 的数组 metadata 语义一致。"""
    from backend.rag.retrieval.hybrid import _filter_by_metadata

    docs = [
        Document(
            page_content="命中",
            metadata={"doc_id": "d1", "person_names": '["张三", "李四"]'},
        ),
        Document(
            page_content="不命中",
            metadata={"doc_id": "d2", "person_names": '["王五"]'},
        ),
    ]

    result = _filter_by_metadata(docs, {"person_names": "张三"})

    assert [doc.metadata["doc_id"] for doc in result] == ["d1"]
