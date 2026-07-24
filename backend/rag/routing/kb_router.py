"""KB Router — 根据用户问题路由到候选知识库。

v1: 关键词权重计分，<5ms，零 LLM 成本。
升级时只需替换 route() 实现，接口不变。
"""

from __future__ import annotations

import time
from typing import Dict, List

from backend.config.kb_rules import KB_ROUTING_RULES, FALLBACK_KB, MAX_KB_CANDIDATES
from backend.shared.logger import logger


class KBRouter:
    """知识库路由器。

    用法:
        router = KBRouter()
        result = router.route("库存怎么盘点")
        # → {"candidates": [{kb_id, confidence, reason}], "fallback": False, ...}
    """

    def __init__(self, rules: Dict[str, List[str]] | None = None):
        self.rules = rules or KB_ROUTING_RULES
        self.version = "keyword_v1"

    def route(self, query: str) -> dict:
        """返回候选 KB 列表 + 置信度。

        Returns:
            {"candidates": [{"kb_id": str, "confidence": float, "reason": [str]}],
             "fallback": bool,
             "router_version": str,
             "duration_ms": int}
        """
        t0 = time.time()
        query_lower = query.lower()
        scores: dict[str, int] = {}

        for kb_id, keywords in self.rules.items():
            hits = 0
            matched: list[str] = []
            for kw in keywords:
                if kw.lower() in query_lower:
                    hits += 1
                    matched.append(kw)
            if hits > 0:
                scores[kb_id] = hits
                logger.debug(f"[KBRouter] {kb_id}: {hits} hits ({matched})")

        if not scores:
            duration_ms = int((time.time() - t0) * 1000)
            return {
                "candidates": [{"kb_id": FALLBACK_KB, "confidence": 1.0, "reason": ["fallback: 无关键词命中"]}],
                "fallback": True,
                "router_version": self.version,
                "duration_ms": duration_ms,
            }

        total_hits = sum(scores.values())
        candidates = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:MAX_KB_CANDIDATES]

        result = [
            {
                "kb_id": kb_id,
                "confidence": round(hits / total_hits, 2),
                "reason": [f"keyword:{hits} hits"],
            }
            for kb_id, hits in candidates
        ]

        duration_ms = int((time.time() - t0) * 1000)
        return {
            "candidates": result,
            "fallback": False,
            "router_version": self.version,
            "duration_ms": duration_ms,
        }
