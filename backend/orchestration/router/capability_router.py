"""CapabilityRouter：复用既有域内候选与细路由，不执行 capability。"""
from __future__ import annotations

from typing import Any

from backend.orchestration.router.hierarchical import (
    HierarchicalRouter,
    resolve_domain_tools,
)
from backend.orchestration.router.models import CapabilityDecision


_DOMAIN_ALIASES = {
    "travel_booking": "travel",
    "travel_commerce": "travel",
}


class CapabilityRouter:
    """只返回候选 capability 与评分，禁止调用 Tool/Skill/Workflow。"""

    def __init__(self, hierarchical_router: HierarchicalRouter | None = None):
        self._hierarchical = hierarchical_router or HierarchicalRouter()

    def route(
        self,
        domain: str,
        query: str,
        context: dict[str, Any] | None = None,
    ) -> CapabilityDecision:
        """在一个已经确定的域内复用既有 FineToolRouter。"""

        del context  # 现有细路由接口只消费 query/domain，保留参数供未来兼容。
        canonical_domain = _DOMAIN_ALIASES.get(domain, domain)
        candidates = resolve_domain_tools(canonical_domain)
        if not candidates:
            return {
                "domain": domain,
                "capability": None,
                "candidates": [],
                "confidence": 0.0,
                "source": "registry",
                "reasoning": f"domain={canonical_domain} 无已注册候选",
            }

        selection = self._hierarchical.select_tool(
            query, canonical_domain, candidates,
        )
        by_name = {candidate.name: candidate for candidate in candidates}
        candidate_rows = []
        for name in selection.candidate_tools:
            candidate = by_name[name]
            score = (
                selection.fine_top1_score
                if name == selection.fine_top1
                else 0.3
            )
            candidate_rows.append({
                "name": name,
                "score": round(float(score), 3),
                "risk": candidate.risk_level,
                "source": "hierarchical",
            })

        # capability 是域内 top1 hint，不代表已经执行；灰区仍保留全部候选。
        capability = selection.fine_top1 or None
        return {
            "domain": domain,
            "capability": capability,
            "candidates": candidate_rows,
            "confidence": float(selection.tool_confidence),
            "source": "hierarchical",
            "reasoning": (
                f"domain={canonical_domain} fine_top1={selection.fine_top1} "
                f"route_mode={selection.route_mode}"
            ),
        }

    @staticmethod
    def from_routing_meta(
        domain: str,
        meta: dict[str, Any],
    ) -> CapabilityDecision:
        """从既有 hierarchical 元数据派生候选，不重复向量检索。"""

        names = list(meta.get("candidate_tools") or [])
        top1 = str(meta.get("fine_top1") or "")
        top1_score = float(meta.get("fine_top1_score") or 0.0)
        rows = [
            {
                "name": name,
                "score": round(top1_score if name == top1 else 0.3, 3),
                "risk": "UNKNOWN",
                "source": "hierarchical",
            }
            for name in names
        ]
        return {
            "domain": domain,
            "capability": top1 or None,
            "candidates": rows,
            "confidence": top1_score,
            "source": "hierarchical",
            "reasoning": str(meta.get("reason_code") or "hierarchical metadata"),
        }

    @staticmethod
    def from_route_decision(
        domain: str,
        decision: Any,
    ) -> CapabilityDecision:
        """把 legacy RouteDecision 变成候选快照，不触发任何执行。"""

        if hasattr(decision, "model_dump"):
            decision = decision.model_dump()
        if not isinstance(decision, dict):
            decision = {}
        rows = []
        for candidate in decision.get("candidates") or []:
            if hasattr(candidate, "model_dump"):
                candidate = candidate.model_dump()
            if not isinstance(candidate, dict):
                continue
            rows.append({
                "name": str(candidate.get("name") or ""),
                "score": float(candidate.get("score") or 0.0),
                "risk": "UNKNOWN",
                "source": "legacy",
            })
        capability = rows[0]["name"] if rows else None
        return {
            "domain": domain,
            "capability": capability,
            "candidates": rows,
            "confidence": float(decision.get("confidence") or 0.0),
            "source": "legacy",
            "reasoning": str(decision.get("reason") or "legacy RouteDecision"),
        }


__all__ = ["CapabilityRouter"]
