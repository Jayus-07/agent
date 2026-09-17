"""customer_service/router/coarse_router.py — 客服粗分类路由

2 层 fallback（2026-09-17 删除 Chroma 向量层，全站存储收口 pgvector；
种子语义路由随 512 维轨一并移除）:
  1. Rule: CS_DOMAIN_KEYWORDS 关键词计数 → 直接决定
  2. Hint: DomainDetector 预检的域命中作为弱兜底
  3. UNKNOWN: 无匹配（交由上层 LLM 兜底）
"""
from __future__ import annotations

from backend.customer_service.router.types import CSDomain


class CSCoarseRouter:
    """客服域粗分类 — 输出 CSDomain。"""

    def classify(self, query: str, rule_hint: CSDomain | None = None) -> tuple[CSDomain, float, str]:
        """粗分类入口。

        Returns:
            (domain, confidence, reason)
        """
        from backend.config.customer_service import (
            CS_CONFIDENCE_CAUTIOUS,
            CS_DOMAIN_KEYWORDS,
        )

        domain, conf, reason = self._rule_classify(query, CS_DOMAIN_KEYWORDS)
        # P2.1（audit #157）：规则决定线 0.8 → CS_CONFIDENCE_CAUTIOUS（0.6）。
        # 向量层删除后规则通道是唯一判定，规则命中即采信。
        if conf > 0:
            return domain, conf, f"rule: {reason}"

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
