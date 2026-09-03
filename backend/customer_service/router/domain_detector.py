"""customer_service/router/domain_detector.py — 客服域检测器

双通道保守策略:
  - 规则通道: CS_DOMAIN_PATTERNS 正则匹配, ≥CS_RULE_MIN_HITS → rule_pass
  - 向量通道: Chroma 相似度 ≥ CS_VECTOR_THRESHOLD → vector_pass
  - is_cs = rule_pass AND vector_pass (保守: 误判代价远高于漏判)
"""
from __future__ import annotations

from backend.customer_service.router.types import CSDetection
from backend.shared.logger import logger


class DomainDetector:
    """判断 query 是否属于客服域。"""

    def __init__(self):
        self._collection = None
        self._ensure_index()

    def _ensure_index(self) -> None:
        try:
            from langchain_chroma import Chroma
            from backend.rag.embedding_singleton import get_embedding
            from backend.config.customer_service import CS_ROUTER_INDEX_DIR
            from backend.customer_service.router.seeds import CS_DOMAIN_SEEDS
            from pathlib import Path

            Path(CS_ROUTER_INDEX_DIR).mkdir(parents=True, exist_ok=True)
            embedding = get_embedding()
            self._collection = Chroma(
                collection_name="cs_domain_v1",
                embedding_function=embedding,
                persist_directory=CS_ROUTER_INDEX_DIR,
            )

            if self._collection._collection.count() == 0:
                texts, metadatas = [], []
                for domain, seeds in CS_DOMAIN_SEEDS.items():
                    for s in seeds:
                        texts.append(s)
                        metadatas.append({"domain": domain})
                if texts:
                    self._collection.add_texts(texts=texts, metadatas=metadatas)
                    logger.info(f"[DomainDetector] 已建域索引: {len(texts)} 条种子")
        except Exception as e:
            logger.warning(f"[DomainDetector] 索引初始化失败: {e}")
            self._collection = None

    def detect(self, query: str) -> CSDetection:
        """双通道检测 query 是否属于客服域。"""
        from backend.config.customer_service import (
            CS_DOMAIN_PATTERNS, CS_RULE_MIN_HITS, CS_VECTOR_THRESHOLD,
        )

        rule_hits, rule_score = self._rule_channel(query, CS_DOMAIN_PATTERNS)
        vector_score = self._vector_channel(query)

        rule_pass = len(rule_hits) >= CS_RULE_MIN_HITS
        vector_pass = vector_score >= CS_VECTOR_THRESHOLD

        is_cs = rule_pass and vector_pass

        reason_parts = []
        if rule_pass:
            reason_parts.append(f"rule={len(rule_hits)}hits")
        if vector_pass:
            reason_parts.append(f"vec={vector_score:.2f}")

        return CSDetection(
            is_cs=is_cs,
            rule_hits=rule_hits,
            rule_score=min(len(rule_hits) / 5.0, 1.0),
            vector_score=vector_score,
            reason="; ".join(reason_parts) if reason_parts else "no_match",
        )

    def _rule_channel(self, query: str, patterns: dict) -> tuple[list[str], float]:
        hits = []
        for domain, regexes in patterns.items():
            for rx in regexes:
                if rx.search(query):
                    hits.append(domain)
                    break
        return hits, min(len(hits) / 3.0, 1.0)

    def _vector_channel(self, query: str) -> float:
        if self._collection is None or self._collection._collection.count() == 0:
            return 0.0
        try:
            results = self._collection.similarity_search_with_score(query, k=1)
            if not results:
                return 0.0
            _, distance = results[0]
            return 1.0 / (1.0 + distance)
        except Exception as e:
            logger.debug(f"[DomainDetector] 向量检索失败: {e}")
            return 0.0


_detector_instance: DomainDetector | None = None


def get_domain_detector() -> DomainDetector:
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = DomainDetector()
    return _detector_instance
