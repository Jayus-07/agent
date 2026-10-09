"""contracts.py — CS LLM 语义理解层契约（2026-10-08 客服域 LLM 收口改造）。

任务书 §六：LLM 只能输出白名单字段，禁止任意附加字段被业务层消费。

与规则理解层（context/understanding/types.py::CSUnderstanding）的关系：
规则层产出确定性理解结果（实体/槽位缺口/情绪信号），本层产出 **LLM 候选**
（intent candidate / 语义槽位 candidate），两者经编排层（semantic.py）合并；
所有 candidate 必须过 validator 白名单才允许进入 cs_route.metadata。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class CSUnderstandingSource(str, Enum):
    """单轮理解来源（trace cs_understanding_source 取值口径）。

    rule          规则明确判定，LLM 0 次调用（Rule First 达成）
    rule_llm      规则判出意图，LLM 补充语义槽位被采纳
    llm_fallback  规则 miss/低置信，LLM 意图补判被采纳
    unchanged     理解层未触发（开关关闭/pending 短路等），零 LLM
    """
    RULE = "rule"
    RULE_LLM = "rule+llm"
    LLM_FALLBACK = "llm_fallback"
    UNCHANGED = "unchanged"


# 语义槽位白名单 —— LLM 只允许产出这些 name（任务书 §九示例字段族）。
# 显式排除一切真实业务 ID（order_id/refund_id/ticket_id/user_id/tenant_id/
# handoff_ticket_id/confirmation_id）：真实 ID 只能来自认证上下文/DB/
# Business Service/Context Resolver（任务书 §十）。
SLOT_NAME_WHITELIST: frozenset[str] = frozenset({
    "product_reference",     # 商品指称：「耳机」
    "time_reference",        # 时间指称：「昨天买的」
    "ordinal",               # 序数指称：「第二个」→ "2"（仅候选，绑定仍由 ContextResolver）
    "attribute",             # 属性修饰：「红色的」「大号」
    "issue_description",     # 问题描述短语
    "desired_resolution",    # 期望解决方式：「退款」「补发」
    "reason",                # 事由（售后/投诉语境）
})

# 真实业务 ID 字段黑名单 —— 键名级拒绝（双保险之一；值形态拒绝在 validator）。
FORBIDDEN_SLOT_NAMES: frozenset[str] = frozenset({
    "order_id", "order_no", "refund_id", "ticket_id", "user_id", "tenant_id",
    "handoff_ticket_id", "confirmation_id", "action_id", "case_id",
})


class CSSlotCandidate(BaseModel):
    """单个语义槽位候选（LLM 输出经 validator 清洗后的形态）。"""
    name: str
    value: str
    confidence: float = 0.0
    evidence_text: str = ""
    source: str = "llm_slot_enrichment"


class CSUnderstandingResult(BaseModel):
    """单轮 LLM 语义理解结果（任务书 §六契约的落地形态）。

    本结果只是 candidate 集合：intent_candidate 需经确定性代码按
    INTENT_PROFILES 画像改写 cs_route；slots 需经 resolver 用真实业务
    数据解析成对象（唯一绑定/多候选追问/无候选不猜）。
    """
    intent_candidate: Optional[str] = None
    slots: list[CSSlotCandidate] = Field(default_factory=list)
    sentiment: Optional[str] = None
    complaint_severity: Optional[str] = None
    requires_clarification: bool = False
    source: CSUnderstandingSource = CSUnderstandingSource.UNCHANGED
    confidence: float = 0.0
    task_plan_candidate: Optional[dict[str, Any]] = None
    task_plan_source: Optional[str] = None

    def to_trace_fields(self) -> dict[str, Any]:
        """trace/metrics 低基数字段（不含用户原文与槽位值本身）。"""
        return {
            "cs_understanding_source": self.source.value,
            "cs_llm_intent_used": bool(self.intent_candidate),
            "cs_llm_slot_used": bool(self.slots),
            "cs_llm_slot_candidate_count": len(self.slots),
            "cs_task_plan_source": self.task_plan_source or "",
            "cs_task_count": len(
                (self.task_plan_candidate or {}).get("tasks") or []
            ),
        }
