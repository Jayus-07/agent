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
            from pathlib import Path

            from langchain_chroma import Chroma

            from backend.config.customer_service import CS_ROUTER_INDEX_DIR
            from backend.customer_service.router.seeds import CS_DOMAIN_SEEDS
            from backend.rag.embedding_singleton import get_embedding

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
            CS_DOMAIN_PATTERNS,
            CS_RULE_MIN_HITS,
            CS_VECTOR_DECIDE,
            CS_VECTOR_THRESHOLD,
        )

        rule_hits, rule_score = self._rule_channel(query, CS_DOMAIN_PATTERNS)
        vector_score = self._vector_channel(query)

        rule_pass = len(rule_hits) >= CS_RULE_MIN_HITS
        vector_pass = vector_score >= CS_VECTOR_THRESHOLD
        # 双通道组合判定：规则≥min_hits 且向量过阈值；或向量强匹配单独决定
        # （对齐 coarse_router 的 ≥0.85 决定语义，避免单关键词强语义被漏判）
        is_cs = (rule_pass and vector_pass) or vector_score >= CS_VECTOR_DECIDE

        reason_parts = []
        if rule_pass:
            reason_parts.append(f"rule={len(rule_hits)}hits")
        if vector_score >= CS_VECTOR_DECIDE:
            reason_parts.append(f"vec_decide={vector_score:.2f}")
        elif vector_pass:
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


# ── 廉价规则预判（无 embedding 调用）──────────────────────────
def cs_rule_hit_count(query: str) -> int:
    """CS 域规则的命中数（纯正则，~1ms，不触发向量通道）。

    2026-09-15 性能优化新增：CS 检测器的向量通道每次请求一次云端
    embedding 往返（实测 1.0~3.4s）。调用方可先用本函数做廉价预判——
    规则命中才继续走向量检测，否则先给更廉价的高精度预过滤
    （如旅游域纯正则）让路，避免白烧 embedding。
    """
    from backend.config.customer_service import CS_DOMAIN_PATTERNS

    if not query:
        return 0
    hits, _ = get_domain_detector()._rule_channel(query, CS_DOMAIN_PATTERNS)
    return len(hits)


# ── 检测结果缓存（同 query 短 TTL 复用，省 embedding 往返）──────
_DETECT_CACHE: dict[str, tuple[float, "CSDetection"]] = {}
_DETECT_CACHE_TTL = 300.0
_DETECT_CACHE_MAX = 512


def detect_cached(query: str) -> "CSDetection":
    """detect() 的带缓存包装（进程内 TTL 缓存，仅按 query 维度）。

    检测结果只取决于 query（is_cs/分数），与 session 无关；灰度/路由
    判定在调用方，故缓存安全。失败自动旁路直调，不影响正确性。
    """
    import time as _t

    key = (query or "").strip()
    if not key:
        return get_domain_detector().detect(query)

    hit = _DETECT_CACHE.get(key)
    if hit is not None:
        ts, det = hit
        if _t.monotonic() - ts < _DETECT_CACHE_TTL:
            return det

    det = get_domain_detector().detect(query)
    if len(_DETECT_CACHE) >= _DETECT_CACHE_MAX:
        # 简单容量控制：清掉最旧的一批（避免引入额外依赖）
        for k in list(_DETECT_CACHE)[: _DETECT_CACHE_MAX // 4]:
            _DETECT_CACHE.pop(k, None)
    _DETECT_CACHE[key] = (_t.monotonic(), det)
    return det
