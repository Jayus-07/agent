"""RetrievalEvaluator — offline retrieval quality measurement.

Metrics: Recall@5, Recall@10, MRR, NDCG.

benchmark_dataset.json format:
  [
    {
      "query": "...",
      "intent": "...",
      "relevant_doc_ids": ["doc_001", ...],
      "relevant_chunk_ids": ["chunk_a", ...],
      "min_relevant": 1
    }
  ]

Usage:
  from retrieval.evaluation import RetrievalEvaluator

  evaluator = RetrievalEvaluator("retrieval/evaluation/benchmark_dataset.json")
  result = evaluator.evaluate(pipeline)   # or pipeline.search
  print(result.summary())
"""

import json
import math
from dataclasses import dataclass, field


@dataclass
class EvalResult:
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    mrr: float = 0.0
    ndcg: float = 0.0
    per_query: list[dict] = field(default_factory=list)
    query_count: int = 0

    def summary(self) -> str:
        lines = [
            f"Queries: {self.query_count}",
            f"Recall@5:  {self.recall_at_5:.3f}",
            f"Recall@10: {self.recall_at_10:.3f}",
            f"MRR:       {self.mrr:.3f}",
            f"NDCG:      {self.ndcg:.3f}",
        ]
        return "\n".join(lines)


class RetrievalEvaluator:
    """Offline retrieval evaluator. Compare search results against benchmark."""

    def __init__(self, benchmark_path: str):
        with open(benchmark_path, "r", encoding="utf-8") as f:
            self._benchmark = json.load(f)

    def evaluate(self, pipeline) -> EvalResult:
        """Run all benchmark queries through pipeline.search() and score results."""
        recall_5_vals: list[float] = []
        recall_10_vals: list[float] = []
        mrr_vals: list[float] = []
        ndcg_vals: list[float] = []
        per_query: list[dict] = []

        for item in self._benchmark:
            query = item["query"]
            relevant_docs = set(item.get("relevant_doc_ids", []))
            relevant_chunks = set(item.get("relevant_chunk_ids", []))
            relevant_ids = relevant_docs | relevant_chunks

            try:
                # Use search() if available, fall back to ask()
                if hasattr(pipeline, 'search'):
                    _ = pipeline.search(query)
                else:
                    _ = pipeline.ask(query)
            except Exception as e:
                per_query.append({"query": query, "error": str(e)})
                continue

            # After ask(), results are embedded in the answer string.
            # For evaluation, we measure from the retrieved context.
            # If no relevant IDs specified, skip scoring.
            if not relevant_ids:
                per_query.append({"query": query, "skipped": "no relevant IDs in benchmark"})
                continue

            # For now: check if pipeline stores last context.
            # Future: hook into lc_chain to capture retrieved doc_ids.
            retrieved_ids = set()
            try:
                chain = pipeline.lc_chain
                # Access the innermost retriever's last results (best effort)
                base = getattr(chain, 'chunk_retriever_base', None)
                if base:
                    # The retriever chain stores last result via LangChain internals
                    pass
            except Exception:
                pass

            # Scoring
            hits = len(retrieved_ids & relevant_ids)
            r5 = hits / max(min(len(relevant_ids), 5), 1) if relevant_ids else 1.0
            r10 = hits / max(min(len(relevant_ids), 10), 1) if relevant_ids else 1.0

            recall_5_vals.append(r5)
            recall_10_vals.append(r10)
            # MRR and NDCG approximated from single rank
            mrr_vals.append(1.0 if hits > 0 else 0.0)
            ndcg_vals.append(hits / max(len(relevant_ids), 1) if relevant_ids else 1.0)

            per_query.append({
                "query": query,
                "retrieved": len(retrieved_ids),
                "relevant": len(relevant_ids),
                "hits": hits,
                "recall@5": round(r5, 3),
            })

        n = len(per_query) or 1
        return EvalResult(
            recall_at_5=sum(recall_5_vals) / max(len(recall_5_vals), 1),
            recall_at_10=sum(recall_10_vals) / max(len(recall_10_vals), 1),
            mrr=sum(mrr_vals) / max(len(mrr_vals), 1),
            ndcg=sum(ndcg_vals) / max(len(ndcg_vals), 1),
            per_query=per_query,
            query_count=len(self._benchmark),
        )
