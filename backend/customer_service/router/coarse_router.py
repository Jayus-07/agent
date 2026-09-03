"""customer_service/router/coarse_router.py — 客服粗分类路由

3 层 fallback:
  1. Rule: CS_DOMAIN_KEYWORDS 关键词计数, ≥2 命中 → 直接决定
  2. Vector: Chroma 语义匹配, ≥0.85 → 决定, ≥0.6 → 采纳
  3. LLM: 兜底 (V1 降级为 UNKNOWN)
"""
from __future__ import annotations

from backend.customer_service.router.types import CSDomain
from backend.shared.logger import logger


class CSCoarseRouter:
    """客服域粗分类 — 输出 CSDomain。"""

    def __init__(self):
        self._collection = None
        self._ensure_index()

    def _ensure_index(self) -> None:
        try:
            from pathlib import Path

            from langchain_chroma import Chroma

            from backend.config.customer_service import CS_ROUTER_INDEX_DIR
            from backend.customer_service.router.seeds import CS_COARSE_SEEDS
            from backend.rag.embedding_singleton import get_embedding

            Path(CS_ROUTER_INDEX_DIR).mkdir(parents=True, exist_ok=True)
            embedding = get_embedding()
            self._collection = Chroma(
                collection_name="cs_coarse_v1",
                embedding_function=embedding,
                persist_directory=CS_ROUTER_INDEX_DIR,
            )

            if self._collection._collection.count() == 0:
                texts, metadatas = [], []
                for domain, seeds in CS_COARSE_SEEDS.items():
                    for s in seeds:
                        texts.append(s)
                        metadatas.append({"domain": domain})
                if texts:
                    self._collection.add_texts(texts=texts, metadatas=metadatas)
                    logger.info(f"[CSCoarseRouter] 已建粗索引: {len(texts)} 条种子")
        except Exception as e:
            logger.warning(f"[CSCoarseRouter] 索引初始化失败: {e}")
            self._collection = None

    def classify(self, query: str, rule_hint: CSDomain | None = None) -> tuple[CSDomain, float, str]:
        """粗分类入口。

        Returns:
            (domain, confidence, reason)
        """
        from backend.config.customer_service import CS_DOMAIN_KEYWORDS

        domain, conf, reason = self._rule_classify(query, CS_DOMAIN_KEYWORDS)
        if conf >= 0.8:
            return domain, conf, f"rule: {reason}"

        domain_v, conf_v, reason_v = self._vector_classify(query)
        if conf_v >= 0.85:
            return domain_v, conf_v, f"vector: {reason_v}"
        if conf_v >= 0.6:
            return domain_v, conf_v, f"vector_moderate: {reason_v}"

        if rule_hint and rule_hint != CSDomain.UNKNOWN:
            return rule_hint, 0.5, f"hint_fallback: {rule_hint.value}"

        return CSDomain.UNKNOWN, 0.0, "no_match"

    def _rule_classify(self, query: str, keywords: dict) -> tuple[CSDomain, float, str]:
        best_domain, best_count = CSDomain.UNKNOWN, 0
        for domain, kws in keywords.items():
            count = sum(1 for kw in kws if kw in query)
            if count > best_count:
                best_domain, best_count = CSDomain(domain), count

        if best_count >= 2:
            conf = min(best_count / 3.0, 1.0)
            return best_domain, conf, f"{best_domain.value}({best_count}hits)"
        return CSDomain.UNKNOWN, 0.0, ""

    def _vector_classify(self, query: str) -> tuple[CSDomain, float, str]:
        if self._collection is None or self._collection._collection.count() == 0:
            return CSDomain.UNKNOWN, 0.0, "no_index"
        try:
            results = self._collection.similarity_search_with_score(query, k=3)
            if not results:
                return CSDomain.UNKNOWN, 0.0, "no_results"

            domain_scores: dict[str, list[float]] = {}
            for doc, distance in results:
                d = doc.metadata.get("domain", "")
                score = 1.0 / (1.0 + distance)
                domain_scores.setdefault(d, []).append(score)

            best_domain, best_score = CSDomain.UNKNOWN, 0.0
            for d, scores in domain_scores.items():
                avg = sum(scores) / len(scores)
                if avg > best_score:
                    try:
                        best_domain = CSDomain(d)
                    except ValueError:
                        continue
                    best_score = avg

            return best_domain, best_score, f"top={best_domain.value}({best_score:.2f})"
        except Exception as e:
            logger.debug(f"[CSCoarseRouter] 向量检索失败: {e}")
            return CSDomain.UNKNOWN, 0.0, "error"
