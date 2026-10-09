"""tests/travel/test_plan_lifecycle.py — 生命周期状态机守护测试（v4 §3.1）

冻结口径：10 态 + 合法迁移表 + fail-fast。非法迁移必须抛
IllegalPlanTransition，不允许静默放行。
"""
from datetime import datetime, timezone

import pytest

from backend.travel.core.plan_lifecycle import (
    LEGAL_TRANSITIONS,
    RESERVED_STATUSES,
    IllegalPlanTransition,
    history_entry,
    transition,
)
from backend.travel.core.plan_lifecycle import (
    TravelPlanStatus as S,
)


class TestHappyPath:
    def test_full_chain_to_confirmed(self):
        current = S.DRAFT
        for target in (
            S.COLLECTING_REQUIREMENTS,
            S.RESEARCHING,
            S.PLANNING,
            S.OPTIMIZING,
            S.WAITING_CONFIRMATION,
            S.CONFIRMED,
        ):
            current = transition(current, target)
        assert current is S.CONFIRMED

    def test_clarify_self_loop_legal(self):
        assert transition(S.COLLECTING_REQUIREMENTS, S.COLLECTING_REQUIREMENTS) is (
            S.COLLECTING_REQUIREMENTS
        )

    def test_modify_from_waiting_confirmation(self):
        # 用户修改：brief 槽变 / 行程级调整 / 排程类，三个重入点全部合法
        for target in (S.RESEARCHING, S.PLANNING, S.OPTIMIZING):
            assert transition(S.WAITING_CONFIRMATION, target) is target

    def test_confirmed_reentry_on_modify(self):
        # 已确认计划修改：出新版本后重新待确认
        assert transition(S.CONFIRMED, S.WAITING_CONFIRMATION) is S.WAITING_CONFIRMATION

    def test_waiting_draft_can_be_discarded(self):
        assert transition(S.WAITING_CONFIRMATION, S.DISCARDED) is S.DISCARDED


class TestIllegalTransitions:
    @pytest.mark.parametrize(
        ("current", "target"),
        [
            (S.DRAFT, S.CONFIRMED),
            (S.DRAFT, S.OPTIMIZING),
            (S.RESEARCHING, S.OPTIMIZING),  # 跳段禁止
            (S.OPTIMIZING, S.CONFIRMED),  # 必须经 WAITING_CONFIRMATION
            (S.CONFIRMED, S.COMPLETED),  # 必须经 TRAVELING
            (S.CONFIRMED, S.ARCHIVED),
            (S.ARCHIVED, S.DRAFT),  # 终态不可迁出
            (S.WAITING_CONFIRMATION, S.TRAVELING),  # 未确认不得出发
            (S.DISCARDED, S.WAITING_CONFIRMATION),  # 放弃是终态
        ],
    )
    def test_raises(self, current, target):
        with pytest.raises(IllegalPlanTransition):
            transition(current, target)


class TestReservedStatuses:
    def test_reserved_set_frozen(self):
        assert RESERVED_STATUSES == frozenset(
            {S.TRAVELING, S.COMPLETED, S.ARCHIVED}
        )

    def test_reserved_chain_itself_legal(self):
        # 保留态的链内迁移合法（枚举与迁移表冻结，激活另立项）
        assert transition(S.CONFIRMED, S.TRAVELING) is S.TRAVELING
        assert transition(S.TRAVELING, S.COMPLETED) is S.COMPLETED
        assert transition(S.COMPLETED, S.ARCHIVED) is S.ARCHIVED


class TestTableIntegrity:
    def test_all_states_have_rules(self):
        assert set(LEGAL_TRANSITIONS) == set(S)
        assert len(S) == 11

    def test_history_entry_shape(self):
        at = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
        entry = history_entry(S.DRAFT, S.COLLECTING_REQUIREMENTS, "first_message",
                              reason="新会话", at=at)
        assert set(entry) == {"from", "to", "at", "event", "reason"}
        assert entry["from"] == "draft"
        assert entry["to"] == "collecting_requirements"
        assert entry["at"] == at.isoformat()  # 注入时钟被尊重
        # checkpoint dict 纪律：条目可 JSON 序列化
        import json

        json.dumps(entry, ensure_ascii=False)
