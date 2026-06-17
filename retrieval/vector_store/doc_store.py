"""DocStore — thin wrapper over existing data/doc_db/ (document-level ChromaDB)."""

from langchain_core.documents import Document


class DocStore:
    """Encapsulates the existing document-level ChromaDB (data/doc_db/).

    No rebuilding — wraps the doc_db instance passed by RAGPipeline.
    """

    def __init__(self, doc_db):
        self._db = doc_db

    def search(self, query: str, k: int = 5, filter: dict | None = None) -> list[Document]:
        return self._db.similarity_search(query, k=k, filter=filter)

    def get_by_ids(self, doc_ids: list[str]) -> list[Document]:
        """Retrieve full documents by doc_id."""
        try:
            results = self._db.get(where={"doc_id": {"$in": doc_ids}})
            return [
                Document(page_content=text, metadata=results["metadatas"][i])
                for i, text in enumerate(results["documents"])
            ]
        except Exception:
            return []

    def count(self) -> int:
        try:
            return self._db._collection.count()
        except Exception:
            return -1
