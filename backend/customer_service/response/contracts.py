"""contracts.py — CS Response Composer 契约（任务书 §十七~§二十一）。

LLM 输入必须来自结构化事实（ExpertResult.data），输出只允许 answer 字段
（任务书 §十九 Reply Contract）——composer 不允许模型改写业务状态、
不允许新增 Tool 未返回的业务事实（P0-18 有订单号守卫兜底）。
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ResponsePolicy(str, Enum):
    """按 Expert 类型的回复策略（任务书 §二十一，硬编码不配置化——
    策略本身是治理红线，不允许运行时改写）。"""
    TEMPLATE = "template"          # action / handoff：模板直出，0 LLM
    LLM_PREFERRED = "llm_preferred"    # query：Tool 事实 → LLM 转自然语言
    LLM_WITH_GUARD = "llm_with_guard"  # complaint：安抚 → LLM + OutputGuard 兜底
    LLM_UPSTREAM = "llm_upstream"  # knowledge：RAG 链已是 LLM 组织，直通不二次润色


# expert 语义名 → 回复策略（knowledge/query=LLM preferred、
# action/handoff=Template preferred、complaint=LLM+policy guard）
EXPERT_RESPONSE_POLICY: dict[str, ResponsePolicy] = {
    "knowledge": ResponsePolicy.LLM_UPSTREAM,
    "query": ResponsePolicy.LLM_PREFERRED,
    "action": ResponsePolicy.TEMPLATE,
    "handoff": ResponsePolicy.TEMPLATE,
    "complaint": ResponsePolicy.LLM_WITH_GUARD,
}


class CSResponseContext(BaseModel):
    """composer 的结构化输入（任务书 §十九 CSResponseContext）。"""
    intent: str = ""
    user_message: str = ""
    facts: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict] = Field(default_factory=list)
    action_status: str | None = None
    pending_action: dict | None = None
    handoff_state: str | None = None
    draft: str = ""  # 模板初稿（LLM 失败时的 fallback 与事实基线）


class CSRenderedResponse(BaseModel):
    """composer 的唯一合法输出（任务书 §十九：只能输出 answer）。"""
    answer: str
