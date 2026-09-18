"""人名倒排索引初始化回归测试。"""

from types import SimpleNamespace


def test_build_person_index_is_called_before_chain_creation(monkeypatch):
    """RAGPipeline 初始化时必须把可用的人名索引传给 RAGChain。"""
    import backend.rag.pipeline as pipeline_module

    pipeline = object.__new__(pipeline_module.RAGPipeline)
    pipeline.doc_db = SimpleNamespace()
    pipeline.vectordb = SimpleNamespace()
    pipeline.bm25 = SimpleNamespace()
    pipeline.bm25_store = SimpleNamespace(
        load=lambda k: pipeline.bm25,
        is_stale=False,
        docs=[],
        doc_count=lambda: 1,
        get_content_hash=lambda: "",
    )
    pipeline.bm25.docs = []
    pipeline.chunk_retriever = None
    pipeline._person_to_doc_cache = {}
    pipeline._ensure_docs_loaded = lambda: setattr(pipeline, "docs", [])
    pipeline._build_bm25_corpus_from_vectorstore = lambda: []
    pipeline._build_person_index = lambda: {"张三": ["doc-1"]}

    class FakeChain:
        def __init__(self, **kwargs):
            self.person_index = kwargs["person_index"]

    monkeypatch.setattr(pipeline_module, "RAGChain", FakeChain)
    monkeypatch.setattr(pipeline_module, "BM25Store", lambda: pipeline.bm25_store)
    monkeypatch.setattr(pipeline_module, "CustomRetriever", lambda _: None)

    pipeline._init_retrievers()

    assert pipeline.person_index == {"张三": ["doc-1"]}
    assert pipeline.lc_chain.person_index is pipeline.person_index
