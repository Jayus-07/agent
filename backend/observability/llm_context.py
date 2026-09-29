"""llm_context.py — LLM 用量的业务归因上下文（M5 / 台账 D5）

为什么存在：llm_usage 明细表已有 user/tenant/trace/run 等归因，但没有
skill_id / tool_id / agent_domain——成本六维只能算三维，答不了「哪个
Skill/Tool/域在烧钱」。

机制：ContextVar（与 ``shared/processing_context.py`` 同款模式）。
设定方在调用 LLM 前包 ``llm_attribution_scope(...)``；记账方
（``llm_usage_store.current_usage_attribution``）读取后随用量落库。
并发安全（asyncio task / 线程各自隔离），未设定时读取为空串。

只依赖标准库 + typing，禁止 import 业务模块（会被底层 proxy 反向消费）。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterator

_UNSET = ""


@dataclass(frozen=True)
class LLMAttribution:
    """当前 LLM 调用的业务归属（都可为空=未归因）。"""

    skill_id: str = _UNSET
    tool_id: str = _UNSET
    agent_domain: str = _UNSET


_attribution_var: ContextVar[LLMAttribution | None] = ContextVar(
    "llm_attribution", default=None
)

#: 已知业务域白名单（管理端 group_by 维度口径；域图注册键）
KNOWN_DOMAINS = frozenset({
    "customer_service", "travel", "travel_commerce", "travel_booking",
    "selection_funnel", "workflow", "general",
})


def get_llm_attribution() -> LLMAttribution:
    """读取当前归因；未设定时返回全空实例（永不抛错）。"""
    return _attribution_var.get() or LLMAttribution()


@contextmanager
def llm_attribution_scope(
    *,
    skill_id: str | None = None,
    tool_id: str | None = None,
    agent_domain: str | None = None,
) -> Iterator[LLMAttribution]:
    """临时绑定归因（叠加语义：未传的键沿用上层绑定，便于
    ``skill → tool`` 嵌套时 skill_id 保持、tool_id 收窄）。

    退出时恢复上层上下文，不影响外层记账。
    """
    upper = get_llm_attribution()
    merged = LLMAttribution(
        skill_id=skill_id if skill_id is not None else upper.skill_id,
        tool_id=tool_id if tool_id is not None else upper.tool_id,
        agent_domain=agent_domain if agent_domain is not None else upper.agent_domain,
    )
    token: Token[LLMAttribution | None] = _attribution_var.set(merged)
    try:
        yield merged
    finally:
        _attribution_var.reset(token)


__all__ = [
    "LLMAttribution",
    "KNOWN_DOMAINS",
    "get_llm_attribution",
    "llm_attribution_scope",
]
