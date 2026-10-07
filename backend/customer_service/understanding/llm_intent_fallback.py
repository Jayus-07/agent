"""llm_intent_fallback.py — 规则 miss/低置信时的 LLM 意图补判（任务书 §八）。

Rule First：规则强命中（confidence ≥ CS_CONFIDENCE_CAUTIOUS 且 intent 非
unknown）时本函数不应被调用（由 semantic.py 编排保证，LLM = 0 次）。

输出约束：
  - LLM 只能返回 {"intent": "...", "confidence": 0.xx}
  - intent 白名单 = router/intents.py::INTENT_PROFILES 键集（动态构造进
    prompt，禁止 prompt 单独维护一份会漂移的意图表）
  - 禁止输出 next_node / tool / execute 之类编排字段（validator 白名单
    之外整体丢弃）
"""
from __future__ import annotations

from backend.customer_service.understanding.llm_runtime import (
    llm_invoke_once,
    mask_for_llm,
    render_understanding_prompt,
)
from backend.customer_service.understanding.validator import (
    extract_json_object,
    validate_intent_candidate,
)
from backend.shared.logger import logger


def intent_fallback_enabled() -> bool:
    from backend.config.customer_service import CS_UNDERSTANDING_LLM_ENABLED

    return CS_UNDERSTANDING_LLM_ENABLED


def rule_intent_is_confident(intent: str, confidence: float) -> bool:
    """Rule First 判定：规则已明确 → LLM 0 次。

    规则明确 = intent 非 unknown 且 confidence ≥ Supervisor 谨慎闸门
    （闸门两侧行为与现状对齐：0.6 以下本来就会被 L6/L7 拦截/兜底）。
    """
    from backend.config.customer_service import CS_CONFIDENCE_CAUTIOUS

    return bool(intent) and intent != "unknown" and confidence >= CS_CONFIDENCE_CAUTIOUS


def llm_intent_candidate(masked_query: str) -> tuple[str, float] | None:
    """LLM 意图补判。返回白名单内的 (intent, llm_self_reported_confidence)
    或 None（无效输出/超时/异常一律 None，由调用方走规则降级）。

    注意：返回的 confidence 是 LLM 自报值，只进 trace 观测，**不得直接
    驱动路由决策**（路由置信度由 semantic.py 取固定保守值）。
    """
    from backend.config.customer_service import (
        CS_UNDERSTANDING_LLM_TIMEOUT_MS,
    )
    from backend.customer_service.router.intents import INTENT_PROFILES

    if not masked_query.strip():
        return None

    candidates = "\n".join(
        f"- {key}: {profile.domain.value}"
        for key, profile in INTENT_PROFILES.items()
    )
    prompt = render_understanding_prompt(
        "customer_service.intent_understanding",
        user_message=masked_query[:300],
        intent_candidates=candidates,
    )
    if prompt is None:
        return None

    content = llm_invoke_once(prompt, CS_UNDERSTANDING_LLM_TIMEOUT_MS)
    if content is None:
        return None

    data = extract_json_object(content)
    if data is None:
        logger.warning("[CS IntentFallback] 非法 JSON，丢弃")
        return None

    validated = validate_intent_candidate(
        data.get("intent"), frozenset(INTENT_PROFILES.keys()),
    )
    if validated is None:
        logger.warning(
            "[CS IntentFallback] 意图不在白名单: %r", data.get("intent"),
        )
        return None

    try:
        confidence = float(data.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    intent, _ = validated
    return intent, max(0.0, min(confidence, 1.0))
