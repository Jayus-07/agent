"""customer_service/response — CS Response Composer（2026-10-08 LLM 收口改造）。

Expert 结构化结果 → 按 Expert 策略生成自然回复；OutputGuard 仍在
reporter 最后执行（P0-20），composer 产出只是 guard 的输入。
"""
from backend.customer_service.response.contracts import (
    EXPERT_RESPONSE_POLICY,
    CSRenderedResponse,
    CSResponseContext,
    ResponsePolicy,
)
from backend.customer_service.response.composer import compose_reply

__all__ = [
    "EXPERT_RESPONSE_POLICY",
    "CSRenderedResponse",
    "CSResponseContext",
    "ResponsePolicy",
    "compose_reply",
]
