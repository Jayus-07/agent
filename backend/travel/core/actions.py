"""travel/core/actions.py — Supervisor 意图枚举与入口映射（v3 §5.3 冻结）

Phase 1 只立枚举与映射表（零行为）：现有全部入口**等价归入**对应 action。
Phase 3 起 supervisor 决策链消费本枚举；BOOK 收编在 Phase 5（flag
TRAVEL_UNIFIED_INTENT），QUERY 接线在 Phase 6（assistant）。判定保持
规则化，不引入 LLM 意图分类（确定性优先）。
"""
from __future__ import annotations

import enum


class SupervisorAction(str, enum.Enum):
    PLAN = "plan"
    MODIFY = "modify"
    QUERY = "query"
    BOOK = "book"
    CANCEL = "cancel"


# 现有入口 → action 等价映射（v3 §5.3 表的代码化；键为入口语义说明）。
# 图内 checkpoint 续跑不经 action（延续当前 run，保留现机制）。
ENTRY_ACTION_MAPPING: dict[str, SupervisorAction] = {
    "travel_prefilter: 新命中（无 pending）": SupervisorAction.PLAN,
    "pending_resolver: avoid_patch": SupervisorAction.MODIFY,
    "pending_resolver: 值型补槽": SupervisorAction.MODIFY,
    "pending_resolver: cancel": SupervisorAction.CANCEL,
    "continuation_resolver: 继续未完成 run": SupervisorAction.MODIFY,
}
# Phase 5 追加：booking_prefilter 命中 → BOOK（flag 控制）。
# Phase 6 追加：QUERY 问答入口 → QUERY。
