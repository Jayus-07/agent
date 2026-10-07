"""llm_confirm_candidate.py — 确认/取消语义候选（任务书 §十三「可以」项）。

定位：确认词表（vocab CONFIRM/CANCEL_KEYWORDS）已覆盖 P0 案例的常规
模糊表达（「可以，就这样」「算了，不弄了」）；本模块只在规则判定
NONE（非疑问句）时提供 LLM semantic candidate，且**默认关闭**
（CS_CONFIRM_LLM_CANDIDATE_ENABLED=false）。

红线：
  - LLM 只能输出 {"decision": "confirm"|"cancel"|"unknown"}
  - 最终决策必须经确定性 parser（confirmation.ConfirmationIntent）消费，
    LLM 不修改 pending action 内容、不直接驱动状态机
"""
from __future__ import annotations

from backend.customer_service.understanding.llm_runtime import (
    llm_invoke_once,
    mask_for_llm,
    render_understanding_prompt,
)
from backend.customer_service.understanding.validator import (
    extract_json_object,
    validate_confirm_decision,
)
from backend.shared.logger import logger


def confirm_candidate_enabled() -> bool:
    from backend.config.customer_service import CS_CONFIRM_LLM_CANDIDATE_ENABLED

    return CS_CONFIRM_LLM_CANDIDATE_ENABLED


def llm_confirm_decision(text: str) -> str | None:
    """规则 NONE 时的确认语义候选。返回 "confirm" | "cancel" | "unknown"
    或 None（不可用/无效输出——调用方维持规则结果 NONE→reask）。
    """
    from backend.config.customer_service import CS_UNDERSTANDING_LLM_TIMEOUT_MS

    masked = mask_for_llm(text)
    if not masked.strip():
        return None
    prompt = render_understanding_prompt(
        "customer_service.confirm_candidate",
        user_message=masked[:120],
    )
    if prompt is None:
        return None

    content = llm_invoke_once(prompt, CS_UNDERSTANDING_LLM_TIMEOUT_MS)
    if content is None:
        return None

    data = extract_json_object(content)
    if data is None:
        logger.warning("[CS ConfirmCandidate] 非法 JSON，维持规则结果")
        return None
    decision = validate_confirm_decision(data.get("decision"))
    if decision is None:
        logger.warning(
            "[CS ConfirmCandidate] 决策不在白名单: %r", data.get("decision"),
        )
        return None
    return decision
