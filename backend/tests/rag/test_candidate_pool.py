"""RAG 定标候选池合并回归。"""

from types import SimpleNamespace

from backend.rag.retrieval.candidate_pool import merge_candidate_docs


def _doc(chunk_id: str, text: str):
    return SimpleNamespace(
        page_content=text,
        metadata={"chunk_id": chunk_id},
    )


def test_candidate_pool_interleaves_vector_and_bm25_without_duplicates():
    vector = [_doc("v1", "向量命中"), _doc("same", "重复内容")]
    bm25 = [_doc("same", "重复内容"), _doc("b1", "关键词命中")]

    merged = merge_candidate_docs(vector, bm25, limit=4, interleave=True)

    assert [doc.metadata["chunk_id"] for doc in merged] == [
        "v1", "same", "b1",
    ]


def test_candidate_pool_default_order_keeps_vector_priority():
    vector = [_doc("v1", "向量命中")]
    bm25 = [_doc("b1", "关键词命中")]

    merged = merge_candidate_docs(vector, bm25, limit=2)

    assert [doc.metadata["chunk_id"] for doc in merged] == ["v1", "b1"]


def test_retrieve_docs_forwards_explicit_candidate_pool_size(monkeypatch):
    from backend.services import rag_server

    captured = {}

    class FakePipeline:
        def retrieve_documents(self, **kwargs):
            captured.update(kwargs)
            return []

    monkeypatch.setattr(rag_server, "_get_pipeline", lambda: FakePipeline())

    req = rag_server.RetrieveDocsRequest(
        question="候选池测试", top_k=8, candidate_k=20,
    )
    rag_server.retrieve_docs(req)

    assert captured["top_k"] == 8
    assert captured["candidate_k"] == 20
