"""customer_service/router/domain_detector.py — 客服域检测器

纯规则保守判定（2026-09-17 删除 Chroma 向量通道，全站存储收口 pgvector；
历史双通道「规则 AND 向量」与向量强匹配单独决定线一并移除）:
  - 规则通道: CS_DOMAIN_PATTERNS 正则匹配, ≥CS_RULE_MIN_HITS → is_cs
  - vector_score 字段保留恒 0.0（CSDetection 消费方兼容）

注意：无关键词命中的语义类客服问法（如"东西坏了咋办"）会漏进通用对话；
若进线率异常，调低 CS_RULE_MIN_HITS（env 可覆盖）或扩充 CS_DOMAIN_PATTERNS。
"""
from __future__ import annotations

from backend.customer_service.router.types import CSDetection


class DomainDetector:
    """判断 query 是否属于客服域。"""

    def detect(self, query: str) -> CSDetection:
        """纯规则检测 query 是否属于客服域。"""
        from backend.config.customer_service import CS_DOMAIN_PATTERNS, CS_RULE_MIN_HITS

        rule_hits, _ = self._rule_channel(query, CS_DOMAIN_PATTERNS)
        rule_pass = len(rule_hits) >= CS_RULE_MIN_HITS

        reason = f"rule={len(rule_hits)}hits" if rule_pass else "no_match"
        return CSDetection(
            is_cs=rule_pass,
            rule_hits=rule_hits,
            rule_score=min(len(rule_hits) / 5.0, 1.0),
            vector_score=0.0,
            reason=reason,
        )

    def _rule_channel(self, query: str, patterns: dict) -> tuple[list[str], float]:
        hits = []
        for domain, regexes in patterns.items():
            for rx in regexes:
                if rx.search(query):
                    hits.append(domain)
                    break
        return hits, min(len(hits) / 3.0, 1.0)


_detector_instance: DomainDetector | None = None


def get_domain_detector() -> DomainDetector:
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = DomainDetector()
    return _detector_instance


# ── 廉价规则预判 ─────────────────────────────────────────────
def cs_rule_hit_count(query: str) -> int:
    """CS 域规则的命中数（纯正则，实测 14~35µs）。"""
    from backend.config.customer_service import CS_DOMAIN_PATTERNS

    if not query:
        return 0
    hits, _ = get_domain_detector()._rule_channel(query, CS_DOMAIN_PATTERNS)
    return len(hits)


# ── 检测结果缓存（同 query 短 TTL 复用）─────────────────────
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
