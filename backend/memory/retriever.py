"""HybridRetriever — embedding + importance + recency → rerank → top_k"""
from numpy import dot
from numpy.linalg import norm
from datetime import datetime, timezone


class HybridRetriever:
    def __init__(self, repo):
        self._repo = repo

    async def retrieve(
        self, query: str, embedding: list[float], user_id: str, top_k: int = 5,
    ) -> list:
        # 1. Recall top_20 from pgvector
        candidates = await self._repo.search_hybrid(embedding, user_id, top_k=20)

        # 2. Compute final score: 0.5×sim + 0.3×importance + 0.2×recency
        scored = []
        now = datetime.now(timezone.utc)
        for record in candidates:
            # cosine similarity
            try:
                emb = record.embedding
                sim = dot(embedding, emb) / (norm(embedding) * norm(emb))
            except (ValueError, ZeroDivisionError):
                sim = 0.5

            # recency: days since last access, capped at 365
            delta_days = (now - record.last_access_at).days
            recency = max(0.0, 1.0 - delta_days / 365.0)

            final = 0.5 * sim + 0.3 * record.importance_score + 0.2 * recency
            scored.append((record, final))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [r for r, _ in scored[:top_k]]
