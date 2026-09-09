"""语义评分基础设施 — SemanticScorer 协议 + 三种实现 + 自动降级链。

评分器优先级（由高到低）:
1. CrossEncoderScorer  — bge-reranker-base，零额外下载
2. EmbeddingScorer     — bge-small-zh-v1.5 embedding cosine
3. LexicalFallbackScorer — bigram Jaccard，无模型依赖

所有评分器内置 sha1 缓存，避免重复编码。
"""
from __future__ import annotations

import hashlib
import math
import os
from typing import Protocol

import numpy as np

from backend.shared.logger import logger


class SemanticScorer(Protocol):
    """语义评分器协议 — 所有实现必须遵循此接口。"""

    def score_pairs(self, queries: list[str], docs: list[str]) -> list[float]:
        """对 (query, doc) 对列表打分，返回 0~1 分数列表。"""
        ...


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


class _CachedScorer:
    """缓存混入 — 子类通过 _score_raw 实现实际评分，本类负责 sha1 缓存。"""

    def __init__(self) -> None:
        self._cache: dict[str, float] = {}

    def _cache_key(self, query: str, doc: str) -> str:
        return f"{_sha1(query)}:{_sha1(doc)}"

    def score_pairs(self, queries: list[str], docs: list[str]) -> list[float]:
        assert len(queries) == len(docs), "queries and docs must have same length"
        results: list[float] = []
        uncached_indices: list[int] = []
        uncached_queries: list[str] = []
        uncached_docs: list[str] = []
        keys: list[str] = []

        for i, (q, d) in enumerate(zip(queries, docs)):
            key = self._cache_key(q, d)
            keys.append(key)
            if key in self._cache:
                results.append(self._cache[key])
            else:
                results.append(0.0)
                uncached_indices.append(i)
                uncached_queries.append(q)
                uncached_docs.append(d)

        if uncached_queries:
            raw_scores = self._score_raw(uncached_queries, uncached_docs)
            for idx, score in zip(uncached_indices, raw_scores):
                results[idx] = score
                self._cache[keys[idx]] = score

        return results

    def _score_raw(self, queries: list[str], docs: list[str]) -> list[float]:
        raise NotImplementedError


class CrossEncoderScorer(_CachedScorer):
    """CrossEncoder 评分器 — 使用 bge-reranker-base + sigmoid 归一化。"""

    def __init__(self) -> None:
        super().__init__()
        from backend.rag.reranker import LocalModelLoader

        already_loaded = LocalModelLoader.is_loaded()
        self._model = LocalModelLoader.get_instance()
        if already_loaded:
            logger.info("[SemanticScorer] CrossEncoder 评分器复用已加载的 reranker 模型")
        else:
            logger.info("[SemanticScorer] CrossEncoder 评分器首次加载模型（评测专用，非 reranker）")

    def _score_raw(self, queries: list[str], docs: list[str]) -> list[float]:
        pairs = list(zip(queries, docs))
        logits = self._model.predict(pairs)
        return [_sigmoid(float(s)) for s in logits]


class EmbeddingScorer(_CachedScorer):
    """Embedding cosine 评分器 — 使用 bge-small-zh-v1.5。"""

    def __init__(self) -> None:
        super().__init__()
        from backend.rag.embedding_singleton import get_embedding

        self._embedding = get_embedding()
        logger.info("[SemanticScorer] Embedding 评分器已加载")

    def _score_raw(self, queries: list[str], docs: list[str]) -> list[float]:
        q_embeds = self._embedding.embed_documents(queries)
        d_embeds = self._embedding.embed_documents(docs)
        q_arr = np.array(q_embeds)
        d_arr = np.array(d_embeds)
        q_norm = np.linalg.norm(q_arr, axis=1, keepdims=True)
        d_norm = np.linalg.norm(d_arr, axis=1, keepdims=True)
        q_norm = np.where(q_norm == 0, 1, q_norm)
        d_norm = np.where(d_norm == 0, 1, d_norm)
        cosine = np.sum((q_arr / q_norm) * (d_arr / d_norm), axis=1)
        return [max(0.0, float(s)) for s in cosine]


class LexicalFallbackScorer(_CachedScorer):
    """Bigram Jaccard 兜底评分器 — 无模型依赖。"""

    def __init__(self) -> None:
        super().__init__()
        logger.info("[SemanticScorer] Lexical fallback 评分器已加载")

    def _score_raw(self, queries: list[str], docs: list[str]) -> list[float]:
        from backend.evaluation.metrics import _string_jaccard

        return [_string_jaccard(q, d) for q, d in zip(queries, docs)]


_SCORER_MAP = {
    "cross_encoder": CrossEncoderScorer,
    "embedding": EmbeddingScorer,
    "lexical": LexicalFallbackScorer,
}


def get_eval_scorer() -> SemanticScorer:
    """根据 EVAL_SEMANTIC_SCORER 环境变量返回评分器，失败时自动降级。

    优先级: cross_encoder → embedding → lexical
    """
    requested = os.getenv("EVAL_SEMANTIC_SCORER", "cross_encoder")
    chain = _degradation_chain(requested)

    for name in chain:
        try:
            return _SCORER_MAP[name]()
        except Exception as e:
            logger.warning(f"[SemanticScorer] {name} 加载失败，降级: {e}")

    raise RuntimeError("所有语义评分器均加载失败")


def _degradation_chain(requested: str) -> list[str]:
    order = ["cross_encoder", "embedding", "lexical"]
    if requested not in _SCORER_MAP:
        return order
    idx = order.index(requested)
    return order[idx:]
