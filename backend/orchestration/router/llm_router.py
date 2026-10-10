"""受候选约束的 LLM 能力分诊。

LLM 只在调用方提供的、由 manifest 与 Skill 注册表共同确认的域内候选中
给出选择提示。模型分数、execution_mode 和任意文本能力名都不参与执行授权。
"""
from __future__ import annotations

import json
from collections.abc import Iterable

from backend.orchestration.router.types import (
    CapabilityScore,
    ExecutionMode,
    RouteDecision,
)


DEFAULT_ROUTER_PROMPT = """你是能力分诊器，只从本次合法候选中选择一个最匹配能力。
不要输出置信度、分数、执行模式、workflow 名称或候选外的能力。
问题：{query}
本次合法候选：{allowed_candidates}
只输出 JSON：{{"candidate":"候选中的完整能力名","reason":"简短原因"}}"""


def _extract_json(text: str) -> dict | None:
    """从 LLM 输出中提取 JSON；失败交由调用方走 clarify。"""
    from backend.shared.json_extractor import extract_json

    return extract_json(text)


class LLMRouter:
    """对动态注册的合法候选做受限选择提示，不给 Fast Path 授权。"""

    def __init__(self, timeout: int | None = None):
        from backend.config import ROUTER_LLM_TIMEOUT

        self.timeout = timeout if timeout is not None else ROUTER_LLM_TIMEOUT

    def route(
        self,
        query: str,
        *,
        domain: str = "",
        allowed_candidates: Iterable[str] | None = None,
    ) -> RouteDecision:
        """只允许从传入域的真实注册候选中选；错误时返回无候选 clarify。"""
        allowed = self._legal_candidates(domain, allowed_candidates)
        if not allowed:
            return self._fallback("no_registered_candidates")

        from backend.infra.timeout import safe_call_with_timeout
        from backend.infra.llm import llm
        from backend.config import ROUTER_LLM_MAX_TOKENS
        from backend.shared.logger import logger

        try:
            router_llm = llm.bind(temperature=0, max_tokens=ROUTER_LLM_MAX_TOKENS)
        except Exception as exc:
            logger.warning("[LLMRouter] 模型初始化失败: %s", type(exc).__name__)
            return self._fallback("model_initialization_failed")
        allowed_text = json.dumps(allowed, ensure_ascii=False)
        try:
            from backend.prompts.service import prompt_service

            prompt = prompt_service.render_sync(
                "router.llm",
                query=query[:200],
                allowed_candidates=allowed_text,
            ).text
            # 老的 DB active prompt 可能还没有候选变量；在本次调用中追加
            # 动态清单，保证旧快照也不会扩大模型可见的合法能力集合。
            if allowed_text not in prompt:
                prompt += f"\n本次合法候选（必须从中选择）：{allowed_text}"
        except Exception:
            prompt = DEFAULT_ROUTER_PROMPT.format(
                query=query[:200], allowed_candidates=allowed_text,
            )

        try:
            raw = safe_call_with_timeout(
                router_llm.invoke,
                timeout=self.timeout,
                default_value=None,
                error_message=f"[LLMRouter] 推理超时 ({self.timeout}s)",
                input=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            logger.warning("[LLMRouter] 推理异常: %s", type(exc).__name__)
            return self._fallback("exception")
        if raw is None:
            return self._fallback("timeout")

        try:
            content = raw.content if hasattr(raw, "content") else str(raw)
            parsed = _extract_json(content)
        except Exception:
            return self._fallback("parse_failed")
        if not isinstance(parsed, dict):
            return self._fallback("parse_failed")

        selected = parsed.get("candidate") or parsed.get("capability")
        if not selected:
            legacy_candidates = parsed.get("candidates") or []
            if legacy_candidates and isinstance(legacy_candidates[0], dict):
                selected = legacy_candidates[0].get("name")
        selected = str(selected or "").strip()
        if selected not in allowed:
            return self._fallback("invalid_or_unregistered_candidate")

        ordered = [selected, *(name for name in allowed if name != selected)]
        return RouteDecision(
            execution_mode=ExecutionMode.PLAN,
            route_mode="llm_selection",
            candidates=[CapabilityScore(name=name, score=0.0) for name in ordered],
            confidence=0.0,
            reason=str(parsed.get("reason") or "LLM 受限候选选择提示"),
            routing_meta={
                "architecture": "llm_router",
                "decision_source": "llm_selection_hint",
                "selection_mode": "llm_selection",
                "candidate_source": "dynamic_registered_domain_candidates",
                "candidate_tools": ordered,
                "score_type": "llm_choice_untrusted",
                "confidence_used": False,
                "fallback_reason": "",
                "block_reason": "",
            },
        )

    @staticmethod
    def _legal_candidates(
        domain: str,
        allowed_candidates: Iterable[str] | None,
    ) -> list[str]:
        """manifest routed ∩ Skill registry ∩ 域内调用方候选。"""
        if not domain or domain in {"unknown", "general"}:
            return []
        try:
            from backend.orchestration.router.capability_router import _DOMAIN_ALIASES
            from backend.orchestration.router.hierarchical import resolve_domain_tools

            canonical_domain = _DOMAIN_ALIASES.get(domain, domain)
            registered = [c.name for c in resolve_domain_tools(canonical_domain)]
            if allowed_candidates is None:
                return registered
            requested = {str(name) for name in allowed_candidates}
            return [name for name in registered if name in requested]
        except Exception:
            return []

    @staticmethod
    def _fallback(reason: str) -> RouteDecision:
        """失败时不填任意业务能力，不把请求送入无目标 Planner。"""
        return RouteDecision(
            execution_mode=ExecutionMode.PLAN,
            route_mode="clarify",
            candidates=[],
            confidence=0.0,
            reason=f"LLM Router 安全降级（{reason}）",
            routing_meta={
                "architecture": "llm_router",
                "decision_source": "llm_failure",
                "selection_mode": "clarify",
                "candidate_source": "none",
                "candidate_tools": [],
                "score_type": "none",
                "confidence_used": False,
                "fallback_reason": reason,
                "block_reason": reason,
            },
        )
