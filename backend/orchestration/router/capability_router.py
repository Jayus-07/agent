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

        context = context or {}
        canonical_domain = _DOMAIN_ALIASES.get(domain, domain)
        candidates = self._authorized_candidates(
            resolve_domain_tools(canonical_domain), context,
        )
        if not candidates:
            return {
                "domain": domain,
                "capability": None,
                "candidates": [],
                "confidence": 0.0,
                "selection_mode": "clarify",
                "top1": "",
                "top1_score": 0.0,
                "top2": "",
                "top2_score": 0.0,
                "margin": 0.0,
                "risk_level": "UNKNOWN",
                "score_type": "none",
                "fallback_reason": "",
                "block_reason": "no_authorized_candidates",
                "source": "registry",
                "reasoning": f"domain={canonical_domain} 无已注册且有权访问的候选",
            }

        selection = self._hierarchical.select_tool(
            query, canonical_domain, candidates,
        )
        by_name = {candidate.name: candidate for candidate in candidates}
        candidate_rows = []
        for name in selection.candidate_tools:
            candidate = by_name[name]
            candidate_rows.append({
                "name": name,
                "score": round(float(selection.candidate_scores.get(name, 0.0)), 3),
                "risk": candidate.risk_level,
                "fast_path_enabled": candidate.fast_path_enabled,
                "permission_ready": CapabilityRouter._permission_ready(name, context),
                "source": "hierarchical",
            })

        # capability 是域内 top1 hint，不代表已经执行；灰区仍保留全部候选。
        capability = selection.fine_top1 or None
        return {
            "domain": domain,
            "capability": capability,
            "candidates": candidate_rows,
            "confidence": float(selection.tool_confidence),
            "selection_mode": selection.route_mode,
            "top1": selection.fine_top1,
            "top1_score": float(selection.fine_top1_score),
            "top2": selection.fine_top2,
            "top2_score": float(selection.fine_top2_score),
            "margin": float(selection.fine_margin),
            "risk_level": selection.top1_risk_level,
            "score_type": selection.score_type,
            "fallback_reason": selection.fallback_reason,
            "block_reason": selection.fast_path_block_reason,
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
        scores = meta.get("candidate_scores") or {}
        details = {
            str(item.get("name") or ""): item
            for item in meta.get("candidate_details") or []
            if isinstance(item, dict) and item.get("name")
        }
        rows = [
            {
                "name": name,
                "score": round(float(scores.get(name, details.get(name, {}).get("score", top1_score if name == top1 else 0.0))), 3),
                "risk": details.get(name, {}).get("risk", meta.get("risk_level", "UNKNOWN") if name == top1 else "UNKNOWN"),
                "fast_path_enabled": bool(details.get(name, {}).get("fast_path_enabled", name == top1 and meta.get("tool_route_mode") == "fast_path")),
                "permission_ready": bool(details.get(name, {}).get("permission_ready", CapabilityRouter._permission_ready(name))),
                "source": "hierarchical",
            }
            for name in names
        ]
        return {
            "domain": domain,
            "capability": top1 or None,
            "candidates": rows,
            "confidence": top1_score,
            "selection_mode": str(meta.get("selection_mode") or meta.get("tool_route_mode") or ""),
            "top1": top1,
            "top1_score": top1_score,
            "top2": str(meta.get("fine_top2") or ""),
            "top2_score": float(meta.get("fine_top2_score") or 0.0),
            "margin": float(meta.get("fine_margin") or 0.0),
            "risk_level": str(meta.get("risk_level") or "UNKNOWN"),
            "score_type": str(meta.get("score_type") or "unknown"),
            "fallback_reason": str(meta.get("fallback_reason") or ""),
            "block_reason": str(meta.get("fast_path_block_reason") or ""),
            "source": "hierarchical",
            "reasoning": str(meta.get("reason_code") or "hierarchical metadata"),
        }

    @staticmethod
    def from_route_decision(
        domain: str,
        decision: Any,
    ) -> CapabilityDecision:
        """把已有 RouteDecision 变成候选快照，不触发任何执行。"""

        if hasattr(decision, "model_dump"):
            decision = decision.model_dump()
        if not isinstance(decision, dict):
            decision = {}
        meta = decision.get("routing_meta") or {}
        details = {
            str(item.get("name") or ""): item
            for item in meta.get("candidate_details") or []
            if isinstance(item, dict) and item.get("name")
        }
        rows = []
        for candidate in decision.get("candidates") or []:
            if hasattr(candidate, "model_dump"):
                candidate = candidate.model_dump()
            if not isinstance(candidate, dict):
                continue
            name = str(candidate.get("name") or "")
            detail = details.get(name, {})
            rows.append({
                "name": name,
                "score": float(candidate.get("score") or 0.0),
                "risk": str(detail.get("risk") or (meta.get("risk_level") if name == meta.get("fine_top1") else "UNKNOWN")),
                "fast_path_enabled": bool(detail.get("fast_path_enabled", False)),
                "permission_ready": bool(
                    detail.get("permission_ready", CapabilityRouter._permission_ready(name))
                ),
                "source": "route_engine",
            })
        capability = rows[0]["name"] if rows else None
        return {
            "domain": domain,
            "capability": str(meta.get("fine_top1") or capability or "") or None,
            "candidates": rows,
            "confidence": float(decision.get("confidence") or 0.0),
            "selection_mode": str(meta.get("selection_mode") or meta.get("tool_route_mode") or ""),
            "top1": str(meta.get("fine_top1") or capability or ""),
            "top1_score": float(meta.get("fine_top1_score") or 0.0),
            "top2": str(meta.get("fine_top2") or ""),
            "top2_score": float(meta.get("fine_top2_score") or 0.0),
            "margin": float(meta.get("fine_margin") or 0.0),
            "risk_level": str(meta.get("risk_level") or "UNKNOWN"),
            "score_type": str(meta.get("score_type") or "unknown"),
            "fallback_reason": str(meta.get("fallback_reason") or ""),
            "block_reason": str(meta.get("fast_path_block_reason") or ""),
            "source": "route_engine",
            "reasoning": str(decision.get("reason") or "RouteDecision 快照"),
        }

    @staticmethod
    def _authorized_candidates(candidates, context: dict[str, Any]):
        """按服务端已验证的角色/范围收窄候选；Tool Governance 仍做执行期复核。"""
        from backend.core.tool_governance.registry import get_tool_spec

        roles = set(context.get("_verified_roles") or ())
        scopes = set(context.get("_verified_scopes") or ())
        allowed = []
        for candidate in candidates:
            spec = get_tool_spec(candidate.name)
            if spec is None:
                continue
            if spec.required_roles and not roles.intersection(spec.required_roles):
                continue
            if spec.required_scopes and not set(spec.required_scopes).issubset(scopes):
                continue
            allowed.append(candidate)
        return allowed

    @staticmethod
    def _permission_ready(name: str, context: dict[str, Any] | None = None) -> bool:
        """复用已绑定的服务端角色；Tool Governance 仍执行时再次校验。"""
        from backend.core.tool_governance.registry import get_tool_spec

        spec = get_tool_spec(name)
        if spec is None:
            return False
        if context is not None:
            roles = set(context.get("_verified_roles") or ())
            scopes = set(context.get("_verified_scopes") or ())
        else:
            try:
                from backend.core.request_context import get_tool_roles

                roles = set(get_tool_roles())
            except Exception:
                roles = set()
            scopes = set()
        if spec.required_roles and not roles.intersection(spec.required_roles):
            return False
        # Tool scope 没有路由层权威来源时 fail closed；不以 data_scope 或
        # RAG 文档权限冒充 Tool scope。
        if not set(spec.required_scopes).issubset(scopes):
            return False
        return True


__all__ = ["CapabilityRouter"]
