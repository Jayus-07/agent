"""llm_slot_enrichment.py — LLM 语义槽位补全（任务书 §九，本轮重点）。

解决「我昨天买的那个耳机怎么还没到？」类自然引用：LLM 只产出**语义
槽位候选**（product_reference/time_reference/ordinal…），绝不产出真实
业务 ID —— 真实对象由 semantic_slots resolver 用真实 user_id 查业务
服务解析（唯一绑定/多候选追问/无候选不猜）。

触发编排（semantic.py）：仅意图落在需要业务对象的查询/动作族，且规则
实体抽取未得到 order_id 时才调用（有显式订单号 = Rule First，0 LLM）。
"""
from __future__ import annotations

from backend.customer_service.understanding.contracts import CSSlotCandidate
from backend.customer_service.understanding.llm_runtime import (
    llm_invoke_once,
    mask_for_llm,
    render_understanding_prompt,
)
from backend.customer_service.understanding.validator import (
    extract_json_object,
    validate_slots,
)
from backend.shared.logger import logger

# 需要业务对象（订单）槽位的意图族 —— LLM slot enrichment 的服务对象。
SLOT_TARGET_INTENTS: frozenset[str] = frozenset({
    "t_order_status", "t_logistics", "as_refund", "as_return", "as_exchange",
})


def slot_enrichment_enabled() -> bool:
    from backend.config.customer_service import CS_SLOT_LLM_ENABLED

    return CS_SLOT_LLM_ENABLED


def intent_wants_slots(intent: str) -> bool:
    return intent in SLOT_TARGET_INTENTS


def llm_slot_candidates(masked_query: str, intent: str) -> tuple[
    list[CSSlotCandidate], int, int, str,
]:
    """LLM 槽位补全。

    Returns:
        (accepted_slots, candidate_count, rejected_count, fallback_reason)
        LLM 不可用/输出无效时返回 ([], 0, 0, reason)。
    """
    from backend.config.customer_service import CS_SLOT_LLM_TIMEOUT_MS

    if not masked_query.strip():
        return [], 0, 0, "empty_input"

    prompt = render_understanding_prompt(
        "customer_service.slot_enrichment",
        user_message=masked_query[:300],
        intent=intent,
    )
    if prompt is None:
        return [], 0, 0, "prompt_unavailable"

    content = llm_invoke_once(prompt, CS_SLOT_LLM_TIMEOUT_MS)
    if content is None:
        return [], 0, 0, "llm_error"

    data = extract_json_object(content)
    if data is None:
        logger.warning("[CS SlotEnrichment] 非法 JSON，丢弃")
        return [], 0, 0, "invalid_json"

    report = validate_slots(data.get("slots"))
    logger.info(
        "[CS SlotEnrichment] accepted=%d rejected=%d reason=%s",
        len(report.accepted), report.rejected_count, report.fallback_reason,
    )
    return (
        report.accepted,
        len(report.accepted) + report.rejected_count,
        report.rejected_count,
        report.fallback_reason,
    )
