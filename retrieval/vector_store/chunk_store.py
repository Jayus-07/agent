"""ChunkStore — thin wrapper over existing data/chroma/ (chunk-level ChromaDB)."""

from langchain_core.documents import Document


class ChunkStore:
    """Encapsulates the existing chunk-level ChromaDB (data/chroma/).

    No rebuilding — wraps the vectordb instance passed by RAGPipeline.
    """

    def __init__(self, vectordb):
        self._db = vectordb

    def search(self, query: str, k: int = 5, filter: dict | None = None) -> list[Document]:
        return self._db.similarity_search(query, k=k, filter=filter)

    def count(self) -> int:
        try:
            return self._db._collection.count()
        except Exception:
            return -1
