# =====================================================
# rag/bm25_retriever.py
# BM25 Sparse Retrieval
# =====================================================

from langchain_community.retrievers import BM25Retriever


def build_bm25_retriever(
        docs,
        k=5
):

    bm25 = BM25Retriever.from_documents(docs)

    bm25.k = k

    return bm25